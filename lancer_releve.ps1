# Releve quotidien. Genere par scripts/releve_quotidien.py --installer.
Set-Location -LiteralPath "C:\Users\natha\Documents\quant-bot"
& "C:\Program Files\Python38\python.exe" "scripts\releve_quotidien.py" --config "config/us.yaml" *>> "data\releve.log"
