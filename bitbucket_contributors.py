import os
import requests
import datetime
import time
import re
import sys
import csv
import platform
from collections import defaultdict
from requests.exceptions import RequestException
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Thread

if platform.system() == "Windows":
    import msvcrt
else:
    import tty
    import termios

REQUEST_LIMIT = 1000
request_counter = 0
reset_time = time.time() + 3600
rate_limit_hits = 0
counter_lock = Lock()

RATE_LIMIT_BACKOFF = [20, 30, 45, 60, 75]  # Adaptive sleep durations

def masked_input(prompt="Password: "):
    print(prompt, end='', flush=True)
    if platform.system() == "Windows":
        result = ''
        while True:
            char = msvcrt.getch()
            if char in {b'\r', b'\n'}:
                print('')
                break
            elif char == b'\x08':
                if result:
                    result = result[:-1]
                    print('\b \b', end='', flush=True)
            elif char == b'\x03':
                raise KeyboardInterrupt
            else:
                try:
                    decoded = char.decode()
                    result += decoded
                    print('*', end='', flush=True)
                except:
                    continue
        return result.strip()
    else:
        password = ''
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ('\r', '\n'):
                    print('')
                    break
                elif ch == '\x7f':
                    if password:
                        password = password[:-1]
                        print('\b \b', end='', flush=True)
                elif ch == '\x03':
                    raise KeyboardInterrupt
                else:
                    password += ch
                    print('*', end='', flush=True)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return password.strip()

def sanitize_input(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    value = value.replace('..', '')
    value = value.replace('/', '_')
    value = value.replace('\\', '_')
    value = value.replace('\n', ' ')
    value = value.replace('\r', '')
    return value

def sanitize_csv(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().replace('\n', ' ').replace('\r', '')
    if value.startswith(('=', '+', '-', '@')):
        value = "'" + value
    return value

def reset_request_counter():
    global request_counter, reset_time
    while True:
        time.sleep(1)
        with counter_lock:
            if time.time() >= reset_time:
                request_counter = 0
                reset_time = time.time() + 3600

def can_make_request():
    with counter_lock:
        return request_counter < REQUEST_LIMIT

def increment_request_counter():
    global request_counter
    with counter_lock:
        request_counter += 1

def rate_limited_get(url, auth, **kwargs):
    global rate_limit_hits
    while not can_make_request():
        print("🚦 Global rate limit hit. Sleeping 10s...")
        time.sleep(10)
    try:
        response = requests.get(url, auth=auth, timeout=10, **kwargs)
        increment_request_counter()
        if response.status_code == 429 or response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            if reset:
                wait_seconds = int(reset) - int(time.time())
            else:
                wait_index = min(rate_limit_hits, len(RATE_LIMIT_BACKOFF) - 1)
                wait_seconds = RATE_LIMIT_BACKOFF[wait_index]
                rate_limit_hits += 1
            wait_seconds = max(wait_seconds, 10)
            print(f"🚦 429 hit. Backing off for {wait_seconds} seconds...")
            time.sleep(wait_seconds)
        return response
    except RequestException:
        print("🌐 Network error. Retrying in 15s...")
        time.sleep(15)
        return None

def verify_credentials(username, app_password):
    response = rate_limited_get("https://api.bitbucket.org/2.0/user", auth=(username, app_password))
    return response and response.status_code == 200

def fetch_all_repositories(workspace, auth):
    repos = []
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}?pagelen=100"
    print("🔍 Fetching repositories...")
    while url:
        response = rate_limited_get(url, auth=auth)
        if not response:
            sys.exit("❌ Network error fetching repositories.")
        if response.status_code in [401, 403]:
            sys.exit("❌ Authentication or permission denied.")
        elif response.status_code != 200:
            sys.exit(f"❌ Failed to fetch repositories: HTTP {response.status_code}")
        data = response.json()
        for repo in data.get("values", []):
            repos.append(sanitize_input(repo["slug"]))
        url = data.get("next")
    return repos

def fetch_commits(workspace, repo, auth, since_date):
    """
    Fetch commits using pagination. The query filters commits newer than 'since_date'
    but since only 100 commits are returned per page, we follow 'next' if available.
    We also break early if a commit is encountered with a date older than 'since_date'
    (assuming commits are ordered descending).
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/commits?q=date>=\"{since_date}\"&pagelen=100"
    all_commits = []
    while url:
        response = rate_limited_get(url, auth=auth)
        if not response:
            break
        if response.status_code != 200:
            print(f"[{repo}] ⚠️ Failed to fetch commits: HTTP {response.status_code}")
            break
        data = response.json()
        commits = data.get("values", [])
        for commit in commits:
            commit_date_full = commit.get("date", "")
            if not commit_date_full:
                continue
            commit_date_str = commit_date_full[:10]  # YYYY-MM-DD
            if commit_date_str < since_date:
                # Since the list is sorted descending, we stop processing further pages.
                url = None
                break
            all_commits.append(commit)
        else:
            url = data.get("next")
    return all_commits

def process_repository(repo, workspace, auth, since_date, global_user_data):
    """
    Process each commit in the repository. For each commit:
    - Skip if no proper author or if the author indicates a bot.
    - Normalize the contributor's name.
    - Skip if the name starts with a digit.
    - Keep track of earliest and latest commit date and the list of repositories.
    """
    commits = fetch_commits(workspace, repo, auth, since_date)
    for commit in commits:
        raw_author = commit.get("author", {}).get("raw", "").strip()
        if not raw_author or re.search(r"\[bot\]|bot@|bot ", raw_author, re.IGNORECASE):
            continue
        match = re.match(r"^(.*?)(?:\s*<(.*?)>)?$", raw_author)
        if not match:
            continue
        name_part = sanitize_input(match.group(1).lower())
        email_part = match.group(2)
        if email_part and "noreply" in email_part.lower():
            continue
        if name_part and name_part[0].isdigit():
            continue
        commit_date_full = commit.get("date", "")
        if not commit_date_full:
            continue
        commit_date_str = commit_date_full[:10]
        if commit_date_str < since_date:
            continue

        user_key = name_part
        display_name = sanitize_csv(raw_author)
        with counter_lock:
            if user_key not in global_user_data:
                global_user_data[user_key] = {
                    "display": display_name,
                    "earliest": commit_date_str,
                    "latest": commit_date_str,
                    "repos": set()
                }
            else:
                if commit_date_str < global_user_data[user_key]["earliest"]:
                    global_user_data[user_key]["earliest"] = commit_date_str
                if commit_date_str > global_user_data[user_key]["latest"]:
                    global_user_data[user_key]["latest"] = commit_date_str
            global_user_data[user_key]["repos"].add(f"{workspace}/{repo}")

def main():
    Thread(target=reset_request_counter, daemon=True).start()

    print("=== Bitbucket Single-Line-Per-Contributor Reporter ===")
    username = sanitize_input(os.environ.get("BB_USER") or input("Enter Bitbucket username: ").strip())
    app_password = os.environ.get("BB_APP_PASSWORD") or masked_input("Enter Bitbucket app password: ")
    workspace = sanitize_input(input("Enter Bitbucket workspace ID: ").strip())

    if not username or not app_password:
        sys.exit("❌ Missing credentials.")

    auth = (username, app_password)
    if not verify_credentials(username, app_password):
        sys.exit("❌ Invalid credentials.")

    # Last 90 days in YYYY-MM-DD format
    since_date = (datetime.datetime.utcnow() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

    repos = fetch_all_repositories(workspace, auth)
    if not repos:
        sys.exit("❌ No repositories found in workspace.")

    # global_user_data holds one entry per contributor
    global_user_data = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for repo in repos:
            futures.append(executor.submit(process_repository, repo, workspace, auth, since_date, global_user_data))
        for future in as_completed(futures):
            pass  # Wait for all repositories to be processed

    total_contributors = len(global_user_data)

    # Write one row per contributor to the CSV file
    with open("bitbucket_contributors_single_line.csv", "w", newline='', encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "Contributor Name/Email",
            "Earliest Commit Date (Last 90d)",
            "Latest Commit Date (Last 90d)",
            "Repositories"
        ])
        for user_key, data in sorted(global_user_data.items()):
            repo_list_str = ", ".join(sorted(data["repos"]))
            writer.writerow([
                data["display"],
                data["earliest"],
                data["latest"],
                sanitize_csv(repo_list_str)
            ])
        writer.writerow([])
        writer.writerow(["Total Unique Contributors", total_contributors])

    print(f"\n✅ Single-line-per-contributor report saved to: bitbucket_contributors_single_line.csv")

if __name__ == "__main__":
    main()