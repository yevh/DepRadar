# DepRadar
Analyze JS dependencies across all public repositories in a GitHub org.

## Features

- Fetches all public repos from a specified GitHub org
- Analyzes npm dependencies in each repository
- Generates HTML report with Dependency graphs and Detailed package info

## Prerequisites

- Python 3.7+
- Node.js and npm
- Git

## How to use

1. Set up your GitHub token:
  ```
  export GITHUB_TOKEN=your_token
  ```
2. Install dep
  ```
  pip install -r requirements.txt
  ```
3. Run
  ```
  python depradar.py github_org_name
  ```
4. Open the report

![exp1](/etc/exp1.png)
![exp2](/etc/exp2.png)
![exp3](/etc/exp3.png)
