# Core Principles

- NEVER create mock data or simplified components unless explicitly told to do so
- NEVER replace existing complex components with simplified versions - always fix the actual problem
- ALWAYS work with the existing codebase - do not create new simplified alternatives
- ALWAYS find and fix the root cause of issues instead of creating workarounds
- NEVER just agree - always state your reasons for choices made. We are a TEAM

# Change Management

- ALWAYS track all changes in CHANGELOG.md
- ALWAYS refer to CHANGELOG.md when working on tasks
- Build new changes into a feature/fix branch off of master
- NEVER commit directly to main or master branches
- Don't add yourself or Claude to git commits

# Testing Requirements

- Always make sure the app builds and starts successfully
- Use a venv for all python testing

# Docker Guidelines

- Build for platform="linux/amd64"
- Docker Hub user and org are ttlequals0
- Always check what the next version should be before tagging (from Docker Hub and CHANGELOG.md)
- Tag format: latest and version matching 'version.py'
- If testing locally, clean up afterwards

# Version Management

- Always update version.py with changes

# Security & Quality

- Always scrub out all sensitive data in the repo
- NO emojis in any code or documentation

# Workspace

- Only work in the podcast-server repository directory
- Extract logs in ~/Downloads NOT /tmp

# Troubleshooting/Analysis

- Always review ALL logs, note potential issues even if it wasn't what was actively asked for
- Use server API to gather information at https://podsrv.ttlequals0.com/api/v1
  - Auth use ./cookies.txt
  - read openapi doc
- to access https://podfeed.ttlequals0.com/ you need a user agent with `PocketCasts` in it.
