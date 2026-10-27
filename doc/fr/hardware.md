# Matériel

*[English version](../en/hardware.md)*

L'intégration lit des entités que Home Assistant possède déjà : elle fonctionne avec tout matériel qui les fournit.
Cette page décrit ce que chaque mesure doit montrer, et un compteur d'eau chaude réalisé avec un ESP8266 et ESPHome.

## Ce dont l'intégration a besoin

| Mesure | Obligatoire | Ce qu'elle doit montrer |
|--------|-------------|-------------------------|
| Relais | oui | L'interrupteur qui alimente la résistance (`switch` ou `input_boolean`), dont l'état suit le contact réel |
| Puissance de la résistance | oui | La puissance consommée par la résistance (W ou kW), mesurée en aval du relais |
| Compteur d'eau chaude | recommandé | Le volume cumulé qui sort du ballon (L ou m³) |
| Énergie de la résistance | facultatif | L'énergie cumulée de la résistance (Wh ou kWh) |

### Relais et puissance

Le ballon plein est détecté à la coupure du thermostat : pendant une chauffe, la puissance passe sous 5 W alors que le
relais reste fermé, pendant 60 s. Le capteur de puissance doit donc :

- mesurer la résistance elle-même, pas toute la maison : une résistance coupée par son thermostat doit lire près de
  0 W ;
- signaler un changement en quelques secondes (un capteur publié seulement toutes les quelques minutes retarde le
  ballon plein et, sans compteur d'énergie, rend l'énergie de chauffe grossière).

Un relais connecté avec mesure de puissance fait les deux. Sans compteur d'énergie, l'énergie est la puissance
intégrée dans le temps ; avec un compteur, c'est son augmentation qui est utilisée, et la puissance ne sert qu'à
détecter la fin de chauffe.

Testé : un Shelly 1PM commande la résistance et la mesure (relais avec mesure de puissance intégrée) ; ses capteurs
de puissance (W) et d'énergie cumulée (kWh) sont la *Puissance de la résistance* et l'*Énergie de la résistance* de
l'intégration.

### Compteur d'eau chaude

Sans compteur d'eau chaude, le niveau ne baisse qu'avec les pertes : un puisage n'est pas vu, le niveau reste trop
haut jusqu'à la fin de chauffe suivante, qui le remet à 100 %. Avec un compteur, chaque litre puisé le fait baisser
tout de suite.

- **Emplacement** : à la **sortie** du ballon, avant tout mitigeur thermostatique. Après le mitigeur, le compteur
  compte de l'eau mélangée (chaude et froide), que l'intégration prendrait pour de l'eau à la consigne.
- **Type** : un compteur prévu pour l'eau chaude (≥ 90 °C), à sortie impulsions (contact sec).
- **Résolution** : 1 impulsion par litre suffit.
- **Comptage** : un total cumulé (`state_class: total_increasing`). L'intégration gère un total qui repart de zéro
  (compteur journalier à minuit, compteur redémarré après un reboot) ; elle ne peut pas retrouver des impulsions qui
  n'ont pas été comptées.

## Exemple : compteur à impulsions sur un ESP8266 avec ESPHome

Montage utilisé sur un ballon de 200 L :

- un compteur d'eau chaude Gioanola 3/4", 1 impulsion par litre, à la sortie du ballon ;
- son contact sec câblé entre GPIO14 (broche D5) et GND d'un Wemos D1 Mini (ESP8266), avec la résistance de tirage interne ;
- la configuration ESPHome [`../examples/esphome-hot-water-meter.yaml`](../examples/esphome-hot-water-meter.yaml).

La configuration publie deux capteurs :

- **le total en litres** (`total_increasing`) : l'entité à donner comme *Compteur d'eau chaude* de l'intégration,
  directement ou à travers un `utility_meter` journalier ;
- **le débit** (L/min, revient à 0 après 10 s sans impulsion), pour les tableaux de bord.

Points importants :

- **Anti-rebond** : le contact rebondit à la fermeture ; les filtres `delayed_on` / `delayed_off` de 50 ms comptent
  chaque litre une seule fois.
- **Redémarrage** : le total est gardé en flash (`restore_value` sur le compteur, `restore_from_flash` sur un
  ESP8266) et republié au démarrage : une mise à jour ou un redémarrage de l'ESP le conserve. Il est écrit au plus
  toutes les 5 min (`flash_write_interval`) : une coupure de courant perd au plus les litres des 5 dernières minutes,
  et le total qui redescend un peu est pris pour une remise à zéro par l'intégration, sans compter deux fois. Sans
  cela, le compteur repart de 0 et reste inconnu jusqu'au premier litre : l'intégration compte à partir de la
  nouvelle valeur, seuls les litres puisés pendant que l'ESP était hors ligne sont perdus. Vérifié le 2026-10-25 :
  1 L puisé, ESP redémarré 18 min plus tard, le total est revenu à 1 L.
