To repozytorium zawiera kompletny prototyp systemu telemetrycznego do symulacji pojazdu, z zaawansowanym zarządzaniem biegami, monitorowaniem wydajności, zużycia paliwa, oraz z nowoczesnym zegarem w stylu Virtual Cockpit inspirowanym rozwiązaniami Audi. Projekt jest w pełni działający, a jego celem jest odwzorowanie zaawansowanego sterowania pojazdem w czasie rzeczywistym, integrującego dane telemetryczne z różnych źródeł.

Kluczowe funkcje systemu:
Dynamiczne przełączanie biegów:

Funkcja shift_gear: Główna logika systemu, odpowiedzialna za zmianę biegów w odpowiedzi na warunki jazdy. Bieg zmienia się na podstawie:
Obrotów silnika (RPM),
Poziomu wciśnięcia pedału gazu,
Poziomu wciśnięcia pedału hamulca,
Trybu jazdy (manualny/automatyczny).
Zmiana biegów:
W górę: Zmiana biegu w górę następuje, gdy obroty silnika przekraczają ustalony próg, zapewniając płynność jazdy.
W dół: Przełączenie na niższy bieg odbywa się, gdy obroty silnika są zbyt niskie lub po wciśnięciu pedału hamulca.
Kickdown: Funkcja, która automatycznie zmienia bieg na niższy, gdy pedał gazu jest wciśnięty do końca, co jest przydatne przy przyspieszaniu.
Zarządzanie trybami jazdy:

Tryb manualny (M): Umożliwia ręczne przełączanie biegów, idealny dla użytkowników preferujących pełną kontrolę nad pojazdem.
Tryb ECO (E) oraz Normal (D)
Tryb sportowy (S): W trybie sportowym, pojazd dostosowuje parametry jak agresywność zmian biegów oraz reakcję na gaz i hamulec, aby dostarczyć bardziej dynamiczną jazdę.
Przełączanie trybów: Dzięki obsłudze trybów, system dostosowuje parametry takie jak moment obrotowy, agresywność zmian biegów, czy reakcje na wciśnięcie pedałów.
Przetwarzanie danych telemetrycznych:

Telemetria: System odbiera dane telemetryczne w czasie rzeczywistym, takie jak:
RPM (obroty silnika),
Prędkość pojazdu,
Stan pedału gazu (poziom wciśnięcia),
Stan pedału hamulca,
Temperatura opon,
Poziom paliwa.
Obliczanie wydajności: Na podstawie telemetrycznych danych, system analizuje różne wskaźniki wydajności, takie jak przyspieszenie, zużycie paliwa, oraz szacunkowy zasięg pojazdu.
Aktualizacja parametrów: System regularnie aktualizuje parametry pojazdu, w tym obroty silnika, prędkość i inne dane w czasie rzeczywistym.
Obliczanie zużycia paliwa i zasięgu:

Zużycie paliwa: Obliczane na podstawie prędkości oraz obrotów silnika, system szacuje, ile paliwa pojazd zużywa w jednostce czasu, zależnie od stylu jazdy.
Szacowanie zasięgu: Na podstawie poziomu paliwa, prędkości, i aktualnego trybu jazdy, system oblicza szacowany zasięg, pokazując, jak daleko pojazd może jeszcze przejechać przy obecnym stanie paliwa.
Komunikacja i zarządzanie wątkami:

Komunikacja przez gniazda UDP: System komunikuje się z zewnętrznymi systemami lub aplikacjami przez protokół UDP, zapewniając odbiór danych telemetrycznych w czasie rzeczywistym.
Zarządzanie wątkami:
Wątek telemetryczny: Odpowiada za odbiór danych i przetwarzanie ich w czasie rzeczywistym.
Wątek zapisu ustawień: W tle zapisuje ustawienia pojazdu, takie jak stan trybu jazdy, bez blokowania głównego wątku.
Optymalizacja: Wykorzystanie wielowątkowości pozwala na równoczesne przetwarzanie danych telemetrycznych i aktualizowanie ustawień pojazdu bez opóźnień w komunikacji.
Zaawansowane logowanie i diagnostyka:

Logowanie: System implementuje zaawansowane logowanie, które zapisuje wszystkie zmiany biegów, stan pedałów, zmiany trybów jazdy oraz inne ważne informacje, które mogą być wykorzystane do analizy wydajności pojazdu.
Błędy i aktualizacje: Logowanie błędów oraz aktualizacji systemowych pozwala na łatwiejszą diagnostykę i śledzenie ewentualnych problemów z systemem.
Zegar VirtualCockpit wzorowany na Audi:

Implementacja zegara: Zaimplementowano cyfrowy zegar inspirowany nowoczesnymi kokpitami Audi, który dynamicznie wyświetla kluczowe dane telemetryczne, takie jak:
RPM,
Prędkość,
Poziom paliwa,
Tryb jazdy.
Interaktywność: Zegar zmienia kolorystykę oraz układ wyświetlanych informacji w zależności od aktualnego trybu jazdy (np. tryb sportowy zmienia kolor na bardziej intensywny).
Estetyka: Zegar został zaprojektowany z myślą o nowoczesnym, minimalistycznym wyglądzie, zapewniając czytelność i elegancki interfejs, wzorowany na cyfrowych kokpitach samochodów Audi.
Struktura kodu:
Funkcja shift_gear: Implementuje logikę przełączania biegów na podstawie różnych parametrów, takich jak RPM, wciśnięcie pedału gazu i hamulca.
Funkcja handle_sport_mode: Obsługuje dynamiczne dostosowanie parametrów pojazdu w trybie sportowym, takich jak agresywność zmian biegów i reakcje na pedały.
Funkcja telemetry_thread: Główny wątek odbierający dane telemetryczne w czasie rzeczywistym i analizujący je w celu aktualizacji systemu.
Funkcja save_settings_worker: Działa w tle, zapisując ustawienia bez blokowania głównego wątku, zapewniając płynność działania systemu.
Funkcja udp_socket: Odpowiada za komunikację przez gniazda UDP, umożliwiając przesyłanie danych do innych systemów w czasie rzeczywistym.
Implementacja zegara VirtualCockpit: Zawiera kod odpowiadający za wyświetlanie zegara w stylu Audi, w tym aktualizowanie wyświetlanych danych w czasie rzeczywistym oraz dostosowywanie wyglądu zegara do trybu jazdy.
