# eBUS Thelia Condens 30+ Monitoring System

**Opis:**  
System ekspercki do monitorowania kotła kondensacyjnego Saunier Duval Thelia Condens 30+ przy użyciu Raspberry Pi Zero 2 W i eBUS Adapter Shield C6. System jest **read-only** i integruje się z **Home Assistant** oraz przechowuje dane w **RRDTool** przez 5 lat.

Funkcjonalności:
- Monitorowanie temperatur zasilania i powrotu CO
- Obliczanie delta T, sprawności i zużycia gazu (m³/h, dziennie, miesięcznie)
- Detekcja taktowania palnika
- Wykrywanie kondensacji (efficiency boost)
- Automatyczne rekomendacje krzywej grzewczej
- Raporty PDF miesięczne
- Analiza porównawcza sezon do sezonu
- Integracja z Home Assistant (sensory, binary sensor, dashboard Lovelace)
- Retencja danych w RRDTool do 5 lat

---

## Struktura repozytorium

📂 Struktura Projektu: ebus-thelia-condens-ha
📄 Pliki główne
requirements.txt – Lista pakietów Python niezbędnych do uruchomienia projektu.

⚙️ Konfiguracja (config/)
ebus.yaml – Definicje komend i rejestrów eBUS.

mqtt.yaml – Ustawienia połączenia z brokerem MQTT (Home Assistant).

📊 Dane i Statystyki (rrd/)
create_rrd.sh – Skrypt powłoki inicjalizujący 5-letnią bazę danych RRD.

🐍 Skrypty Wykonawcze (scripts/)
🚀 main.py – Główny skrypt procesowy zarządzający pętlą programu.

📡 collector.py – Odczyt i parsowanie danych bezpośrednio z szyny eBUS.

🧮 calculations.py – Moduł obliczeniowy (Delta T, estymacja gazu, sprawność).

🔍 diagnostics.py – Analiza poprawności pracy instalacji.

⏱️ taktowanie.py – Wykrywanie zbyt częstych cykli pracy palnika.

💧 condensation.py – Analiza punktu rosy i wydajności kondensacji.

📈 curve_recommendation.py – Inteligentny dobór krzywej grzewczej.

💾 rrd_store.py – Obsługa zapisu danych do bazy RRD.

🌐 mqtt_publish.py – Komunikacja wychodząca do systemów smart home.

📋 monthly_report.py – Silnik generujący raporty w formacie PDF.

⚖️ season_compare.py – Narzędzie do analizy porównawczej rok do roku.

📂 Pozostałe
📂 reports/ – Katalog przechowywania gotowych raportów PDF.

🛠️ systemd/ – Pliki jednostek systemowych.

ebus-collector.service – Skrypt zapewniający pracę programu w tle jako usługa.


---

## Pliki konfiguracyjne

### `config/ebus.yaml`
```yaml
commands:
  flow_temp: boiler.flowtemp
  return_temp: boiler.returntemp
  burner_modulation: burner.modulation
  burner_state: burner.state
  pressure: heating.pressure
```
`config/mqtt.yaml`
```yaml

broker: localhost
base_topic: home/heating
```

## Instalacja środowiska

Skopiuj repo na Raspberry Pi:

```bash
git clone <repo-url> /opt/ebus/ebus-thelia-condens-ha
cd /opt/ebus/ebus-thelia-condens-ha

```

