# コミット時のバージョン管理
コミット時は前回バージョンからの全変更を CHANGELOG.md に記載し、`plugin.json` の version を更新する。
バージョン種別は AskUserQuestion で確認する（minor: 新capability / patch: 改善・修正 / なし: version変更不要）。
CHANGELOG は前回バージョンタグ以降の git diff・コミット履歴から漏れなく記載する。
