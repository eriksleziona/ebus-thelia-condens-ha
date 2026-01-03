def recommend(delta, modulation, taktowanie, condensing):
    if taktowanie and delta < 8:
        return "Obniż krzywą o 0.1–0.2"
    if not condensing:
        return "Za wysoka temp. powrotu"
    if delta > 18:
        return "Podnieś krzywą o 0.1"
    return "Krzywa dobrana prawidłowo"
