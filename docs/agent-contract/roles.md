# Roles

| Role | 権限 | 禁止 |
|---|---|---|
| owner | laneの`owned_paths`を編集・commit | 他laneのpath編集、自己merge |
| reviewer | 指定SHAのdiff・テスト証拠を確認 | owner worktreeの編集 |
| human | ownership変更、merge、release、Issue close | — |

既定ownerはClaude Code。Codexをownerにするのは、ユーザー指定またはCodex固有adapterの変更など
理由が明示されたlaneに限る。
