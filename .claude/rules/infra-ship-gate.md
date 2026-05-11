# インフラ変更の ship ゲート
- diff に buildspec/CDK/Terraform/Lambda 等インフラファイルが含まれる場合、動作確認済みであることを確認してから /ship する
- 本番環境でしかテストできない変更は未検証で ship しない
