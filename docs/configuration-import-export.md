# Configuration Import / Export

Export global settings and feed configuration as JSON for import into the same or another installation. It does not move or replace the database, episodes, processing history, learned patterns, cue templates, audio, or cached artwork.

The JSON export contains configured provider and SMTP credentials, webhook secrets, the global feed-auth key, and private feed source URLs. Protect the file like a password store. The administrator sign-in password, subscriber keys, encryption salt, runtime state, and media assets are not included. Import re-encrypts provider and SMTP credentials with the destination installation's encryption key. An unreadable source credential or unavailable destination encryption prevents import.

Import offers three scopes: global settings and feeds, global settings only, or feeds only. For feeds only, choose all feeds or a selection. It previews the selected changes before applying them. Import merges selected values, preserves omitted values and destination-only feeds, and never deletes feeds. Feed identity conflicts and invalid settings stop the import. The preview becomes stale if a destination setting in the import or selected feed configuration changes, or another import completes. Create a new preview before applying.

The file format is versioned independently of the MinusPod application version. Unknown setting names are shown in preview and skipped. Newer unsupported file formats are rejected. Missing settings leave the destination unchanged. Null clears credentials or resets a supported nullable override; it is invalid for other non-nullable settings. Nullable overrides are:

System One tuning profiles are imported as partial field updates. Fields absent from an older export keep their destination values, and provider profiles not present in the import are retained. A profile-level null resets that profile to its defaults; nullable threshold or limit fields with null keep their documented inherited or unlimited meaning. See [System One](system-one.md#independent-tuning-profiles).

- `verification_model`, `chapters_model`, `secondary_provider`, and `failover_llm_provider`
- `llm_timeout_seconds`, `llm_max_retries`, `secondary_llm_timeout_seconds`, `secondary_llm_max_retries`, `failover_llm_timeout_seconds`, `failover_llm_max_retries`, and `failover_whisper_max_attempts`
- `detection_reasoning_budget` and `detection_reasoning_level`, with corresponding `verification_`, `reviewer_`, `chapter_boundary_`, and `chapter_title_` settings
- `ollama_num_ctx`

`pattern_cleanup_model` has three states: null inherits the detection model, an empty string means the model was explicitly cleared and must be selected before a run, and a nonempty string selects that model. Stage tunables export their effective values, using null only when the effective value is null. Nullable feed overrides can still inherit global defaults. Credentials set through environment variables may remain active after a database credential is cleared.

Configuration import and export do not copy files that a setting may reference, such as locally stored media. Preview and import do not dispatch test webhooks or email notifications.

## UI and API

Open Settings > Data Management > Configuration Import / Export. Acknowledge that the file contains credentials before downloading it. To restore settings, select a JSON file and scope, review the affected setting names and feeds, then apply the import. Feed-refresh warnings appear after the settings are saved.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/system/config-backup` | Download runtime configuration, including configured credentials |
| `POST /api/v1/system/config-import/preview` | Validate and preview a document and selected scope |
| `POST /api/v1/system/config-import` | Apply the same document and scope using its preview token |

Both POST requests take `document` and `scope` (`everything`, `global`, or `feeds`). For `feeds`, pass `selectedFeeds` to choose specific feed slugs; omit it to select all feeds in the file. Apply also requires the `previewToken` returned by preview. Requests are limited to 10 MiB and require authentication and the usual CSRF header. Export checks that the complete configuration fits an import request, including its selection and preview token. An oversized export fails explicitly; no feeds are omitted.

The existing `/api/v1/system/config-export` endpoint remains a redacted diagnostic export. Its output is not a configuration restore file.
