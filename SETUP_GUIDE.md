# AI Studio Proxy API - Complete Setup Guide

## Table of Contents
1. [System Requirements](#system-requirements)
2. [Installation](#installation)
3. [First Launch Configuration](#first-launch-configuration)
4. [Google Account Setup](#google-account-setup)
5. [Authentication Management](#authentication-management)
6. [Troubleshooting](#troubleshooting)

## System Requirements

### Supported Operating Systems
- **Windows 10/11** (64-bit)
- **macOS 10.15+** (Catalina and later)
- **Linux** (Ubuntu 18.04+, Debian 10+, CentOS 8+)

### Required Software
- **Python 3.8+** (3.9+ recommended)
- **Git** (for cloning the repository)
- **Modern web browser** (Chrome, Firefox, Safari, Edge)

### Hardware Requirements
- **RAM:** Minimum 4GB, Recommended 8GB+
- **Storage:** Minimum 2GB free space
- **Network:** Stable internet connection

## Installation

### Step 1: Clone the Repository
```bash
git clone https://github.com/JackHwang/AIstudioProxyAPI.git
cd AIstudioProxyAPI
```

### Step 2: Install Python Dependencies

#### Option A: Using Poetry (Recommended)
```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install

# Activate virtual environment
poetry shell
```

#### Option B: Using pip
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 3: Install Additional Dependencies

#### For Camoufox Browser Automation
```bash
# Install system dependencies for browser automation
# Ubuntu/Debian:
sudo apt-get update
sudo apt-get install -y chromium-browser chromium-chromedriver

# macOS (using Homebrew):
brew install chromium

# Windows:
# Dependencies are bundled with the installation
```

#### For Virtual Display (Linux Only)
```bash
# Ubuntu/Debian:
sudo apt-get install -y xvfb

# CentOS/RHEL:
sudo yum install -y xorg-x11-server-Xvfb
```

### Step 4: Environment Configuration
Create a `.env` file in the project root:
```bash
cp .env.example .env
```

Edit the `.env` file with your preferences:
```env
# Server Configuration
DEFAULT_FASTAPI_PORT=2048
DEFAULT_CAMOUFOX_PORT=9222
DEFAULT_STREAM_PORT=3120

# Launch Mode (headless/debug/virtual_display)
LAUNCH_MODE=headless

# Proxy Configuration (optional)
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890

# Logging
SERVER_LOG_LEVEL=INFO
DEBUG_LOGS_ENABLED=false
```

## First Launch Configuration

### Method 1: GUI Launcher (Recommended for Beginners)

1. **Launch the GUI:**
   ```bash
   python gui_launcher.py
   ```

2. **Initial Setup:**
   - The GUI will open with a three-panel interface
   - Configure your preferred ports in the left panel
   - Set up proxy settings if needed
   - Choose your launch mode

3. **Port Configuration:**
   - **FastAPI Port:** Main API server (default: 2048)
   - **Camoufox Debug Port:** Browser automation (default: 9222)
   - **Stream Proxy Port:** Streaming proxy service (default: 3120)

4. **Launch Modes:**
   - **Headless Mode:** Runs without browser window (requires pre-configured auth)
   - **Debug Mode:** Opens browser for interactive authentication
   - **Virtual Display:** Linux-only headless with virtual display

### Method 2: Command Line Launch

#### Headless Mode (Production)
```bash
python launch_camoufox.py --headless --server-port 2048 --camoufox-debug-port 9222
```

#### Debug Mode (Development/Authentication)
```bash
python launch_camoufox.py --debug --server-port 2048 --camoufox-debug-port 9222
```

#### Virtual Display Mode (Linux)
```bash
python launch_camoufox.py --virtual-display --server-port 2048 --camoufox-debug-port 9222
```

## Google Account Setup

### Step 1: Initial Authentication

1. **Launch in Debug Mode:**
   ```bash
   python launch_camoufox.py --debug
   ```

2. **Browser Authentication:**
   - A Chrome/Chromium browser window will open
   - Navigate to `https://accounts.google.com`
   - Sign in with your Google account
   - Complete any 2FA verification if required

3. **Save Authentication:**
   - After successful login, the terminal will prompt:
   ```
   Do you want to create and save a new authentication file? (y/n; default: n, 15s timeout): y
   Please enter the filename to save (without .json extension, letters/numbers/-/_): my_google_account
   ```
   - Enter a descriptive filename (e.g., `my_google_account`)
   - Press Enter to save

### Step 2: Verify Authentication

1. **Check Saved Files:**
   ```bash
   ls -la auth_profiles/saved/
   ```

2. **Test with Headless Mode:**
   ```bash
   python launch_camoufox.py --headless --active-auth-json my_google_account.json
   ```

### Step 3: Multiple Account Management

#### Adding Additional Accounts
1. Launch in debug mode with a different browser profile:
   ```bash
   python launch_camoufox.py --debug
   ```

2. Sign in with the new Google account
3. Save with a different filename:
   ```
   Please enter the filename to save (without .json extension, letters/numbers/-/_): work_account
   ```

#### Switching Between Accounts
1. **Using GUI:**
   - Click "Manage Authentication Files"
   - Select the desired account from the list
   - Click "Activate Selected File"

2. **Using Command Line:**
   ```bash
   # Switch to personal account
   python launch_camoufox.py --headless --active-auth-json personal_account.json
   
   # Switch to work account
   python launch_camoufox.py --headless --active-auth-json work_account.json
   ```

## Authentication Management

### GUI Authentication Manager

1. **Access Authentication Manager:**
   - In the GUI, click "Manage Authentication Files"
   - The authentication window shows all saved profiles

2. **Available Actions:**
   - **Activate Selected File:** Load an authentication profile for use
   - **Remove Current Auth:** Deactivate the current authentication
   - **Create New Auth File:** Start the authentication creation process

3. **Authentication File Locations:**
   - **Active Profiles:** `auth_profiles/active/`
   - **Saved Profiles:** `auth_profiles/saved/`

### Command Line Authentication Management

#### Listing Available Profiles
```bash
# List saved authentication files
ls auth_profiles/saved/

# List active authentication files
ls auth_profiles/active/
```

#### Activating a Profile
```bash
# Copy saved profile to active directory
cp auth_profiles/saved/my_account.json auth_profiles/active/
```

#### Creating New Profile
```bash
# Interactive authentication creation
python launch_camoufox.py --debug --auto-save-auth --save-auth-as new_profile_name
```

### Authentication File Security

1. **File Permissions:**
   ```bash
   # Set restrictive permissions (Linux/macOS)
   chmod 600 auth_profiles/saved/*.json
   
   # On Windows, ensure files are in a protected user directory
   ```

2. **Backup Authentication:**
   ```bash
   # Create backup directory
   mkdir -p backups/auth_profiles
   
   # Backup all authentication files
   cp -r auth_profiles/saved/ backups/auth_profiles/
   ```

3. **Authentication File Format:**
   Authentication files are stored in JSON format containing:
   - Cookies from Google sessions
   - Browser storage state
   - User session information

## Advanced Configuration

### Proxy Setup

#### HTTP/HTTPS Proxy
```bash
# Set proxy in .env file
HTTP_PROXY=http://proxy-server:port
HTTPS_PROXY=http://proxy-server:port

# Or use command line
python launch_camoufox.py --headless --internal-camoufox-proxy http://proxy-server:port
```

#### GUI Proxy Configuration
1. In the GUI, expand "Proxy Configuration"
2. Enable "Enable Browser Proxy"
3. Enter proxy address (e.g., `http://127.0.0.1:7890`)
4. Click "Test" to verify connectivity

### Service Configuration

#### Helper Service Integration
```bash
# Enable helper service
python launch_camoufox.py --headless --helper http://localhost:3121/getStreamResponse
```

#### Stream Proxy Configuration
```bash
# Configure stream proxy port
python launch_camoufox.py --headless --stream-port 3120

# Disable stream proxy
python launch_camoufox.py --headless --stream-port 0
```

### Logging Configuration

#### Enable Debug Logging
```bash
# Enable detailed logging
python launch_camoufox.py --headless --debug-logs --server-log-level DEBUG
```

#### Log File Locations
- **Launcher Logs:** `logs/launch_app.log`
- **Server Logs:** `logs/server.log`
- **GUI Logs:** `logs/gui_launcher.log`

## Troubleshooting

### Common Issues

#### 1. Port Already in Use
**Error:** `Port 2048 is already in use`

**Solution:**
```bash
# Find process using the port
netstat -tulpn | grep :2048  # Linux
netstat -ano | findstr :2048  # Windows

# Kill the process
kill -9 <PID>  # Linux/macOS
taskkill /PID <PID> /F  # Windows

# Or use different ports
python launch_camoufox.py --headless --server-port 2049
```

#### 2. Authentication File Not Found
**Error:** `Authentication file not found`

**Solution:**
```bash
# Check file exists
ls -la auth_profiles/saved/

# Use absolute path
python launch_camoufox.py --headless --active-auth-json /full/path/to/auth.json

# Or copy to active directory
cp auth_profiles/saved/auth.json auth_profiles/active/
```

#### 3. Browser Automation Fails
**Error:** `Camoufox launch failed`

**Solution:**
```bash
# Install/update browser dependencies
pip install --upgrade camoufox

# Check browser installation
which chromium-browser  # Linux
which chromium  # macOS

# Reinstall browser if needed
sudo apt-get install --reinstall chromium-browser
```

#### 4. Proxy Connection Issues
**Error:** `Proxy connection failed`

**Solution:**
```bash
# Test proxy manually
curl -x http://proxy-server:port http://httpbin.org/ip

# Check proxy configuration
echo $HTTP_PROXY
echo $HTTPS_PROXY

# Disable proxy temporarily
python launch_camoufox.py --headless --internal-camoufox-proxy ""
```

### Debug Mode Troubleshooting

#### Enable Verbose Logging
```bash
# Maximum verbosity
python launch_camoufox.py --debug --debug-logs --trace-logs --server-log-level DEBUG
```

#### Browser Inspection
1. Launch in debug mode:
   ```bash
   python launch_camoufox.py --debug
   ```

2. Open Chrome DevTools:
   - Right-click in the browser window
   - Select "Inspect"
   - Check Console and Network tabs for errors

#### Authentication Issues
1. Clear existing authentication:
   ```bash
   rm -f auth_profiles/active/*.json
   ```

2. Create fresh authentication:
   ```bash
   python launch_camoufox.py --debug --auto-save-auth
   ```

### Performance Optimization

#### Memory Usage
```bash
# Monitor memory usage
htop  # Linux
Activity Monitor  # macOS
Task Manager  # Windows

# Reduce memory usage
python launch_camoufox.py --headless --server-log-level WARNING
```

#### CPU Optimization
```bash
# Limit CPU usage
export CPU_COUNT=2
python launch_camoufox.py --headless
```

### Getting Help

#### Log Analysis
```bash
# View recent logs
tail -f logs/launch_app.log
tail -f logs/server.log

# Search for errors
grep -i error logs/*.log
```

#### Community Support
- **GitHub Issues:** [Repository Issues Page]
- **Documentation:** [Project Wiki]
- **Discord/Telegram:** [Community Links]

## Security Best Practices

### Authentication Security
1. **Never share authentication files**
2. **Use strong, unique passwords for Google accounts**
3. **Enable 2FA on all Google accounts**
4. **Regularly rotate authentication files**
5. **Store authentication files in encrypted directories**

### Network Security
1. **Use HTTPS for all external communications**
2. **Configure firewall rules appropriately**
3. **Use VPN or proxy when necessary**
4. **Monitor network traffic for anomalies**

### System Security
1. **Keep Python and dependencies updated**
2. **Regularly update browser dependencies**
3. **Use antivirus software on Windows**
4. **Monitor system logs for suspicious activity**

## Maintenance

### Regular Updates
```bash
# Update project
git pull origin main

# Update dependencies
poetry update  # or pip install -r requirements.txt --upgrade
```

### Cleanup
```bash
# Clear old logs
find logs/ -name "*.log" -mtime +7 -delete

# Clear browser cache
rm -rf ~/.cache/camoufox/
```

### Backup Strategy
```bash
# Create backup script
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/$DATE"

mkdir -p "$BACKUP_DIR"
cp -r auth_profiles/ "$BACKUP_DIR/"
cp .env "$BACKUP_DIR/"
echo "Backup created: $BACKUP_DIR"
```

---

## Quick Start Summary

For experienced users who want to get started quickly:

```bash
# 1. Clone and install
git clone https://github.com/JackHwang/AIstudioProxyAPI.git
cd AIstudioProxyAPI
poetry install && poetry shell

# 2. Configure environment
cp .env.example .env
# Edit .env with your settings

# 3. Create authentication (first time only)
python launch_camoufox.py --debug --auto-save-auth --save-auth-as main_account

# 4. Run in production mode
python launch_camoufox.py --headless --active-auth-json main_account.json
```

The API server will be available at `http://localhost:2048` (or your configured port).