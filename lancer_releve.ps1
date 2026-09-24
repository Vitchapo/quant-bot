# Releve quotidien du compte papier.
# Genere le 22/09/2026, avec l'interpreteur qui fait deja tourner le robot.
Set-Location -LiteralPath "C:\Users\natha\Documents\quant-bot"
& "C:\Program Files\Python38\python.exe" "scripts\releve_quotidien.py" --config "config/us.yaml" *>> "data\releve.log"
