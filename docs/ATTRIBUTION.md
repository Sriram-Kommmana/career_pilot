# Attribution and licence position

## Independent implementation

All code in this repository is written independently for CareerPilot. No source code,
prompt text, configuration, or file content has been copied from any third-party
project.

## Conceptual references

Two public repositories were studied for architectural ideas:

- **`elbin0324/auto-apply`** — https://github.com/elbin0324/auto-apply
- **`echo0715/dawn`** — https://github.com/echo0715/dawn

As observed on **2026-10-11**:

| Repository | Licence status |
|---|---|
| `elbin0324/auto-apply` | Stated as proprietary, "all rights reserved". No OSS licence file. |
| `echo0715/dawn` | No licence file present. |

Neither repository grants permission to copy, modify, or redistribute its source.
Absent an explicit licence, default copyright applies.

### What this means in practice

1. Neither repository is cloned, vendored, submoduled, or copied into this project.
2. No code, distinctive comments, file-for-file layouts, or prompt text is reproduced.
3. Only general, unprotectable architectural concepts informed this design — for
   example, staging ingestion separately from scoring, or pausing an agent and
   requesting human help when it meets a CAPTCHA.
4. This file exists so the inspiration is acknowledged honestly, without implying a
   licence that was not granted.

Re-verify before publishing this project: a repository can add a licence at any time.

## Third-party dependencies

Runtime and development dependencies are installed from PyPI and npm under their own
licences. See `backend/pyproject.toml` and `frontend/package.json` for the list. No
dependency source is vendored into this repository.
