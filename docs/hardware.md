# Hardware

## ESP32

Dioda debugowania:

- GPIO2 / D2
- stan HIGH zapala diodę

Sterowanie pilotem:

- GPIO26 - przycisk pilota nr 1 / brama lub szlaban 1
- GPIO27 - przycisk pilota nr 2 / brama lub szlaban 2
- impuls domyślny: 700 ms

## Podłączenie do pilota

Dla każdego przycisku pilota należy zrobić osobne zwarcie styków:

- wyjście 1 zwiera styki przycisku nr 1,
- wyjście 2 zwiera styki przycisku nr 2.

Najbezpieczniej zastosować dwa niezależne elementy wykonawcze:

- dwa transoptory,
- albo dwa tranzystory, jeśli masa układu ESP32 i masa pilota mogą być wspólne.

Przed połączeniem mas należy sprawdzić miernikiem, czy przyciski pilota mają wspólny punkt odniesienia. Zgadywanie przy elektronice to tańsza wersja pożaru.
