def analyse(delta, modulation):
    if delta < 5:
        return {"level": "warning", "msg": "Zbyt wysoki przepływ"}
    if delta > 20:
        return {"level": "warning", "msg": "Zbyt niski przepływ"}
    if modulation > 80 and delta < 10:
        return {"level": "info", "msg": "Krzywa za wysoka"}
    return {"level": "ok", "msg": "Instalacja OK"}