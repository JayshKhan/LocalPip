# LocalPip

**LocalPip** is a modern, offline-capable Python package downloader built with PyQt5. It allows you to download Python wheels and their dependencies for specific platforms and Python versions, making it perfect for air-gapped environments or offline development.

![LocalPip Screenshot](https://via.placeholder.com/800x600.png?text=LocalPip+Screenshot)

## Features

- **📦 Smart Dependency Resolution**: Automatically finds and downloads all required dependencies for a package.
- **🎯 Cross-Platform Support**: Download wheels for Windows (`win_amd64`), Linux (`manylinux`), or any other platform.
- **🐍 Python Version Targeting**: Specify which Python version (3.8, 3.9, 3.10, 3.11) you are targeting.
- **✨ specific Version Support**: Need an older version? Search for `package==1.2.3` to get exactly what you need.
- **🛑 Stop Downloads**: Made a mistake? Cancel downloads instantly with a single click.
- **🖥️ Clean UI**: A beautiful, modern interface with a built-in download queue and detailed package information.

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/yourusername/local-pip.git
    cd local-pip
    ```

2.  Install the requirements:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

1.  Run the application:
    ```bash
    python3 pip.py
    ```

2.  **Search**: Enter a package name (e.g., `requests` or `pandas==1.5.0`) in the search bar.
3.  **Configure**: Select your target Python version and Platform from the dropdowns at the bottom.
4.  **Download**: Click the "Download" button. The package and its dependencies will be added to the queue and downloaded to your specified output directory.

## How to Install Downloaded Packages Offline

Once you have downloaded the wheels to a folder (e.g., `~/Downloads/pip-packages`), you can transfer that folder to your offline machine and install them using `pip`.

To install a specific package and its dependencies from your local folder:

```bash
pip install package_name --no-index --find-links /path/to/your/downloaded/folder
```

**Example:**
```bash
# If your wheels are in the current directory
pip install requests --no-index --find-links .
```

## Requirements

- Python 3.8+
- PyQt5
- requests
- packaging

## License

MIT License
