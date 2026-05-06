@echo off
chcp 65001 > nul
title 吉岡商会 車両管理システム - Web公開セットアップ

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║   吉岡商会 車両管理システム  Web公開セットアップ    ║
echo ╚══════════════════════════════════════════════════════╝
echo.
echo このスクリプトでシステムをWeb上に公開します。
echo 所要時間: 約10分
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo 【事前準備】以下2つの無料アカウントが必要です
echo   1. GitHub  → https://github.com
echo   2. Render  → https://render.com
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo GitHubアカウントでログインしてください...
echo.

set PATH=%PATH%;%LOCALAPPDATA%\Programs\GitHub CLI;C:\Program Files\GitHub CLI

:: GitHub CLI ログイン
gh auth login --hostname github.com --git-protocol https --web
if errorlevel 1 (
    echo [エラー] GitHubログインに失敗しました。
    pause
    exit /b 1
)

echo.
echo GitHubログイン成功!
echo.

:: リポジトリ作成
echo リポジトリを作成中...
gh repo create yoshioka-rental --public --description "吉岡商会 車両管理システム" --confirm 2>nul
if errorlevel 1 (
    gh repo create yoshioka-rental --public --description "吉岡商会 車両管理システム" 2>nul
)

:: GitHubにpush
echo コードをGitHubにアップロード中...
git remote add origin https://github.com/%USERNAME%-placeholder%/yoshioka-rental.git 2>nul
for /f "tokens=*" %%i in ('gh api user --jq .login') do set GH_USER=%%i
git remote set-url origin https://github.com/%GH_USER%/yoshioka-rental.git
git branch -M main
git push -u origin main

echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo GitHubへのアップロード完了!
echo.
echo 次にRender.comでデプロイします...
echo ブラウザでRender.comを開きます
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.

start https://render.com/deploy?repo=https://github.com/%GH_USER%/yoshioka-rental

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║   Render.com でのデプロイ手順                       ║
echo ║                                                      ║
echo ║   1. Render.comにGitHub連携でログイン               ║
echo ║   2. "New Web Service" をクリック                   ║
echo ║   3. yoshioka-rental を選択                         ║
echo ║   4. 設定はそのままで "Create Web Service"          ║
echo ║   5. 数分後にURLが発行されます                      ║
echo ╚══════════════════════════════════════════════════════╝
echo.
pause
