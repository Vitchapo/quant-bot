# Lance le robot quantbot. Genere par scripts/robot.py --installer.
Set-Location -LiteralPath "C:\Users\natha\Documents\quant-bot"
& "C:\Program Files\Python38\python.exe" "scripts\robot.py" --config "config/us.yaml" *>> "data\robot_sortie.log"
