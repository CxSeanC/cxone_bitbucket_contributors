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
    # Remove or replace problematic characters
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
            wait_seconds = None
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
        repos.extend([sanitize_input(repo["slug"]) for repo in data.get("values", [])])
        url = data.get("next")
    return repos

def fetch_commits(workspace, repo, auth, since_date):
    # Use the Bitbucket query to filter by date. Note: this only returns the first page (100 commits).
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/commits?q=date>=\"{since_date}\"&pagelen=100"
    retries = 0
    while retries < 5:
        response = rate_limited_get(url, auth=auth)
        if not response:
            retries += 1
            continue
        if response.status_code != 200:
            print(f"[{repo}] ⚠️ Failed to fetch commits: HTTP {response.status_code}")
            return []
        return response.json().get("values", [])
    print(f"[{repo}] ❌ Giving up after retries.")
    return []

def process_repository(repo, workspace, auth, since_date, name_email_map, contributor_set, repo_user_date_map):
    commits = fetch_commits(workspace, repo, auth, since_date)
    for commit in commits:
        raw_author = commit.get("author", {}).get("raw", "").strip()
        if not raw_author or re.search(r"\\[bot\\]|bot@|bot ", raw_author, re.IGNORECASE):
            continue

        match = re.match(r"^(.*?)(?:\s*<(.*?)>)?$", raw_author)
        if not match:
            continue
        name = sanitize_input(match.group(1).strip().lower())

        # Filter out contributor names that start with a number to avoid usernames like "12439234..."
        if not name or name[0].isdigit():
            continue

        email = match.group(2)
        if email and "noreply" in email.lower():
            continue

        display = sanitize_csv(raw_author)
        commit_date_full = commit.get("date", "")
        commit_date_str = commit_date_full[:10] if commit_date_full else ""

        # Ensure the commit is in the last 90 days (client-side fallback)
        if not commit_date_str or commit_date_str < since_date:
            continue

        with counter_lock:
            # Track the unique contributor globally.
            contributor_set.add(name)
            # Map normalized name to display name.
            if name not in name_email_map:
                name_email_map[name] = display

            repo_key = f"{workspace}/{repo}"
            # Update the latest commit date for this contributor in this repository.
            if name in repo_user_date_map[repo_key]:
                if commit_date_str > repo_user_date_map[repo_key][name]:
                    repo_user_date_map[repo_key][name] = commit_date_str
            else:
                repo_user_date_map[repo_key][name] = commit_date_str
    return repo

def main():
    Thread(target=reset_request_counter, daemon=True).start()

    print("=== Bitbucket Contributor Reporter (Optimized) ===")
    username = sanitize_input(os.environ.get("BB_USER") or input("Enter Bitbucket username: ").strip())
    app_password = os.environ.get("BB_APP_PASSWORD") or masked_input("Enter Bitbucket app password: ")
    workspace = sanitize_input(input("Enter Bitbucket workspace ID: ").strip())

    if not username or not app_password:
        sys.exit("❌ Missing credentials.")

    auth = (username, app_password)
    if not verify_credentials(username, app_password):
        sys.exit("❌ Invalid credentials.")

    since_date = (datetime.datetime.utcnow() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

    contributor_set = set()
    name_email_map = {}
    # repo_user_date_map: repository -> {contributor: latest commit date}
    repo_user_date_map = defaultdict(dict)

    repos = fetch_all_repositories(workspace, auth)
    if not repos:
        sys.exit("❌ No repositories found.")

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(process_repository, repo, workspace, auth, since_date,
                            name_email_map, contributor_set, repo_user_date_map)
            for repo in repos
        ]
        for future in as_completed(futures):
            processed_repo = future.result()
            print(f"✅ Processed: {processed_repo}")

    with open("bitbucket_contributors.csv", mode="w", newline='', encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        # Summary table: repository and unique contributor count
        writer.writerow(["Repository", "Unique Contributor Count"])
        for repo, user_dates in sorted(repo_user_date_map.items()):
            writer.writerow([sanitize_csv(repo), len(user_dates)])
        writer.writerow(["Total Unique Contributors", len(contributor_set)])
        writer.writerow([])
        # Detailed table: repository, contributor name, and latest commit date
        writer.writerow(["Repository", "Contributor Name", "Latest Commit Date"])
        for repo, user_dates in sorted(repo_user_date_map.items()):
            for user, commit_date in sorted(user_dates.items()):
                writer.writerow([sanitize_csv(repo), sanitize_csv(name_email_map.get(user, user)), commit_date])
        writer.writerow(["Total Unique Contributors", len(contributor_set)])

    print(f"\n✅ Report saved: bitbucket_contributors.csv")

if __name__ == "__main__":
    main()