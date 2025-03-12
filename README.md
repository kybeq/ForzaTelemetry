# Telemetria Pojazdu z Dynamiczną Skrzynią Biegów (z tybrami jazdy ECO, NORMAL, SPORT, MANUAL) i Zegarem Virtual Cockpit

**Jeśli masz ochotę na wspólne dopracowanie projektu lub masz pomysły na jego rozwój, nie wahaj się skontaktować!
Link do Discorda w opisie. 🚗💻**

**If you're interested in collaborating on this project or have ideas for its development, feel free to reach out!
Discord link in the description. 🚗💻**

To repozytorium zawiera kompletny **prototyp systemu telemetrycznego** do symulacji pojazdu, z zaawansowanym zarządzaniem biegami, monitorowaniem wydajności, zużycia paliwa, oraz z nowoczesnym zegarem w stylu **Virtual Cockpit** inspirowanym rozwiązaniami Audi. Projekt jest w pełni działający, a jego celem jest odwzorowanie zaawansowanego sterowania pojazdem w czasie rzeczywistym, integrującego dane telemetryczne z różnych źródeł.

![forzatelemetry-audi-virtual-cockpit](https://github.com/kybeq/ForzaTelemetry/blob/main/static/image.png)

## Kluczowe Funkcje

### 1. Dynamiczne Przełączanie Biegów
- **Funkcja `shift_gear`**: Odpowiada za zmianę biegów w zależności od:
  - Obrotów silnika (RPM),
  - Poziomu wciśnięcia pedału gazu,
  - Poziomu wciśnięcia pedału hamulca,
  - Trybu jazdy (manualny/automatyczny).
  
- **Zmiana biegów**:
  - **W górę**: Zmiana biegu w górę następuje, gdy obroty silnika przekraczają ustalony próg.
  - **W dół**: Zmiana biegu w dół następuje, gdy obroty silnika są zbyt niskie lub po wciśnięciu pedału hamulca.
  
- **Kickdown**: Funkcja automatycznie zmienia bieg na niższy, gdy pedał gazu jest wciśnięty do końca, co jest przydatne przy przyspieszaniu.

### 2. Zarządzanie Trybami Jazdy
- **Tryb manualny (M)**: Umożliwia ręczne przełączanie biegów, idealny dla użytkowników preferujących pełną kontrolę nad pojazdem.
- **Tryb sportowy (S)**: Dostosowuje parametry, takie jak agresywność zmian biegów, reakcję na pedał gazu i hamulca, zapewniając dynamiczną jazdę.
- **Przełączanie trybów**: System dostosowuje moment obrotowy i reakcję na pedały w zależności od wybranego trybu jazdy.

### 3. Przetwarzanie Danych Telemetrycznych
- **Telemetria**: Odbiera dane telemetryczne w czasie rzeczywistym, takie jak:
  - RPM (obroty silnika),
  - Prędkość pojazdu,
  - Poziom paliwa,
  - Stan pedału gazu i hamulca,
  - Temperatura opon.
  
- **Obliczanie wydajności**: System analizuje wskaźniki wydajności, takie jak przyspieszenie, zużycie paliwa, oraz szacunkowy zasięg pojazdu.
- **Aktualizacja parametrów**: Dane są regularnie aktualizowane w czasie rzeczywistym, aby odzwierciedlały aktualny stan pojazdu.

### 4. Obliczanie Zużycia Paliwa i Zasięgu
- **Zużycie paliwa**: Obliczane na podstawie prędkości oraz obrotów silnika.
- **Szacowanie zasięgu**: Na podstawie poziomu paliwa, prędkości i trybu jazdy, system szacuje, jak daleko pojazd może jeszcze przejechać przy obecnym stanie paliwa.

### 5. Komunikacja i Zarządzanie Wątkami
- **Komunikacja przez gniazda UDP**: System komunikuje się z zewnętrznymi systemami lub aplikacjami przez protokół UDP.
- **Zarządzanie wątkami**:
  - **Wątek telemetryczny**: Odbiera i przetwarza dane telemetryczne w czasie rzeczywistym.
  - **Wątek zapisu ustawień**: Zapisuje ustawienia w tle, nie blokując głównego wątku.

### 6. Zegar VirtualCockpit Wzorowany na Audi
- **Implementacja zegara**: Zegar inspirowany cyfrowymi kokpitami Audi, wyświetlający kluczowe dane telemetryczne:
  - **RPM**,
  - **Prędkość**,
  - **Poziom paliwa**,
  - **Tryb jazdy**.
  
- **Interaktywność**: Zegar zmienia kolorystykę i układ informacji w zależności od aktualnego trybu jazdy (np. tryb sportowy zmienia kolor na bardziej intensywny).
- **Estetyka**: Nowoczesny i minimalistyczny wygląd, zapewniający elegancki interfejs.

## Struktura Kodu

- **Funkcja `shift_gear`**: Implementuje logikę przełączania biegów.
- **Funkcja `handle_sport_mode`**: Obsługuje dynamiczne parametry w trybie sportowym.
- **Funkcja `telemetry_thread`**: Odbiera dane telemetryczne w czasie rzeczywistym.
- **Funkcja `save_settings_worker`**: Zapisuje ustawienia w tle.
- **Funkcja `udp_socket`**: Komunikuje się z zewnętrznymi systemami przez UDP.
- **Implementacja zegara VirtualCockpit**: Wyświetla zegar inspirowany Audi, aktualizując dane w czasie rzeczywistym.

## Licencja
Ten projekt jest dostępny na licencji MIT. Szczegóły w pliku LICENSE.

