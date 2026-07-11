# tender-bot
Telegram бот для тендеров
mkdir bot config data logs
echo. > bot\main.py
echo. > bot\parser.py
echo. > bot\filters.py
echo. > bot\notifier.py
echo. > config\settings.py
echo. > config\keywords.txt
echo. > requirements.txt
echo. > .env
git add .
git commit -m "initial project structure"
git push origin main