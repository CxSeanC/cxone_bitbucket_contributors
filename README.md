# Bitbucket Unique Contributor Reporter

This tool scans all repositories within a Bitbucket workspace and reports the number of unique contributors for each repo over the past 90 days.

## ✅ Features
- Fast and concurrent scanning of all repositories
- Adaptive rate-limit handling (avoids 429 lockouts)
- Secure password input with masking (`*`) support across platforms
- Exports results to `bitbucket_contributors.csv`
- Automatically ignores bots and duplicate names

---

## 🖥 Platform Compatibility
- ✅ Windows  
- ✅ macOS  
- ✅ Linux  

The script detects the platform automatically and uses:
- `msvcrt` for password masking on Windows
- `tty`/`termios` for password masking on Unix-based systems (macOS/Linux)

---

## 🚀 Quick Start

### 1. Clone or Download the Script
Save `bitbucket_contributors.py` to your local machine.

### 2. Install Required Dependencies
You need Python 3.7+ and the following packages:

```bash
pip install requests
```

`requests` is the only required third-party module.

### 3. Create an App Password
Go to: [Bitbucket App Passwords](https://bitbucket.org/account/settings/app-passwords/)

Click **Create App Password** and give it a label (e.g., "Contributor Reporter").

Check these permissions:
- ✅ Account: Read  
- ✅ Workspace membership: Read  
- ✅ Repositories: Read  

Copy and securely save the generated password.

---

## 📥 Usage

Run the script in a terminal:

```bash
python bitbucket_contributors.py
```

You will be prompted for:
- Your Bitbucket username (e.g. `cxseanc`)
- Your app password (input is masked)
- Your Bitbucket workspace ID (e.g. `cxseancjiratest`)

---

## 📄 Output

The script creates `bitbucket_contributors.csv` which includes:
- Each repo's unique contributor count
- A full mapping of repository to contributor names/emails
- A total contributor count summary at the bottom

---

## 🔐 Security

- No credentials are stored.
- Password input is masked using platform-specific logic.
- Input sanitization protects against injection or CSV exploits.

---

## 🌐 Example Run

```
=== Bitbucket Contributor Reporter (Optimized) ===
Enter Bitbucket username: cxseanc
Enter Bitbucket app password: *************
Enter Bitbucket workspace ID: cxseancjiratest
🔍 Fetching repositories...
✅ Processed: calc8066
...
✅ Report saved: bitbucket_contributors.csv
```

---

## 🛠 Troubleshooting

- `ModuleNotFoundError: No module named 'requests'` → Run `pip install requests`
- 429 rate limits → Script will handle it automatically; just wait.
- Windows password masking errors → Handled via built-in compatibility logic.

---

## ⏱ Estimated Runtime for 1,000 Repositories

| Scenario            | Description                                                 | Estimated Time |
|---------------------|-------------------------------------------------------------|----------------|
| ✅ Normal Load       | Active repos w/ average commits, light 429s, steady flow     | **10–15 minutes** |
| ⚠️ Higher Activity   | Many commits in active repos, heavier 429 rate limiting      | **15–25 minutes** |
| 🐢 Network Delays     | VPN/latency, slow API responses, more retries               | **25–35 minutes** |

---

## 📝 License

MIT (Free to use and modify)

---

## 📬 Need Help?

If you encounter any issues, feel free to reach out to Sean or file a request in the project repo.

Happy scanning!
