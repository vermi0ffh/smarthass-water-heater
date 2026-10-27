# Smart Hass - Water Heater

*[English version](../en/README.md)*

Une intégration Home Assistant qui estime l'eau chaude restante dans un chauffe-eau électrique. Elle lit les mesures
que Home Assistant a déjà et **ne commande jamais le chauffe-eau** : vos automatisations, ou un planificateur
d'énergie comme Smart Hass - Energy Optimizer, lisent ses entités pour décider quand chauffer.

> Projet communautaire, **sans affiliation à Home Assistant ni à l'Open Home Foundation, ni soutenu par eux**.
> Home Assistant est une marque de l'Open Home Foundation.

## Comment elle estime

Le ballon est suivi en énergie : ce qui manque pour qu'il soit plein. L'eau chaude puisée (remplacée par de l'eau
froide) et la chaleur que le ballon perd au repos le font monter ; l'énergie de la résistance le fait baisser. Quand
le thermostat coupe — la résistance consommait, puis ne tire plus rien pendant une minute alors que son relais est
toujours allumé — le ballon est plein et l'estimation repart de 100 %. Un relais coupé au milieu d'une chauffe, ou
allumé alors que le thermostat est satisfait, n'est pas pris pour un ballon plein.

Le niveau est donné en litres d'eau à la consigne, et en pourcentage.

## Prérequis

- Home Assistant 2026.10 ou plus récent.
- Un relais qui alimente la résistance (`switch` ou `input_boolean`) et un capteur de la puissance de la résistance.
- Recommandé : un compteur cumulé de l'eau chaude **qui sort du ballon**, avant tout mitigeur (litres ou m³). Sans
  lui, un puisage n'est vu qu'à la fin de chauffe suivante.
- Facultatif : le compteur d'énergie cumulé de la résistance (sinon l'énergie est calculée à partir de la puissance).

Ce que chaque mesure doit montrer, et un exemple de compteur d'eau chaude sur un ESP8266 avec ESPHome :
[Matériel](hardware.md).

## Installation

Avec HACS : ajoutez `https://github.com/vermi0ffh/smarthass-water-heater` comme dépôt personnalisé (catégorie
*Intégration*) — ou [ouvrez-le dans votre Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=vermi0ffh&repository=smarthass-water-heater&category=integration)
—, installez **Smart Hass - Water Heater**, redémarrez Home Assistant, puis *Paramètres → Appareils et services →
Ajouter une intégration*.

## Configuration

| Option | Défaut | Description |
|--------|--------|-------------|
| Nom | — | Le nom de l'appareil |
| Relais | — | L'interrupteur qui alimente la résistance |
| Puissance de la résistance | — | Capteur de puissance (W ou kW) |
| Énergie de la résistance | aucun | Compteur d'énergie cumulé (Wh ou kWh) |
| Compteur d'eau chaude | aucun | Compteur cumulé en sortie de ballon (L ou m³) |
| Volume du ballon | 200 L | |
| Consigne du thermostat | 60 °C | |
| Pertes à l'arrêt | 95 W | Chaleur perdue au repos (93,8 W mesurés sur un ballon de 200 L) |
| Température de l'eau froide | 12 °C | Eau qui entre dans le ballon |
| Apprendre les pertes | désactivé | Compter les pertes mesurées sur votre ballon (capteur *Pertes apprises*) à la place des pertes à l'arrêt ci-dessus |

Les paramètres du ballon se modifient ensuite (*Configurer* sur l'intégration), les entités lues en la reconfigurant
(*Reconfigurer* dans son menu) : l'estimation reste, un compteur remplacé est compté à partir de sa première lecture.

## Entités

| Entité | Unité | Description |
|--------|-------|-------------|
| Eau chaude | L | Eau chaude restante, en litres à la consigne |
| Niveau d'eau chaude | % | La même, en pourcentage du ballon |
| Énergie jusqu'au plein | kWh | Énergie dont la résistance a besoin pour remplir le ballon |
| Temps depuis le plein | h | Depuis le dernier ballon plein (diagnostic) |
| Dernier plein | heure | Quand le ballon a été plein pour la dernière fois (diagnostic) |
| Pertes apprises | W | Les pertes à l'arrêt mesurées sur votre ballon, inconnues jusqu'à un premier cycle (diagnostic, voir plus bas) |
| En demande | on / off | La résistance chaufferait-elle avec le relais allumé (la demande du thermostat, voir plus bas) |

Elles sont **inconnues jusqu'au premier ballon plein** : l'intégration ne devine jamais. Pour démarrer tout de suite,
appelez `smart_hass_water_heater.set_level` avec votre estimation actuelle, ou `smart_hass_water_heater.mark_full`
après une chauffe.

**En demande** : après un ballon plein, le thermostat ne redémarre qu'une fois l'eau refroidie de son hystérésis
(environ 5 °C) : jusque-là, allumer le relais ne chauffe rien. Une fois redémarré, il chauffe jusqu'à la consigne,
même si le relais l'interrompt. L'entité est *mesurée* quand le relais allumé la montre (consommation, ou 60 s sans),
sinon *estimée* à partir de deux seuils appris sur votre ballon — les pertes depuis le plein, et les litres puisés
(l'eau froide qui arrive près de la sonde le fait redémarrer bien plus tôt). Ses attributs donnent les seuils et ce
qu'il reste avant chacun : un planificateur d'énergie sait à partir de quand une chauffe est possible.

**Quand une entité source est indisponible** (ou absente, non numérique, dans une unité non prise en charge) :

- le relais ou la puissance de la résistance, plus de 15 minutes : l'eau chaude, le niveau et l'énergie jusqu'au
  plein passent *indisponibles* (une chauffe pourrait passer inaperçue) jusqu'à son retour, ainsi que la demande ; le
  temps depuis le plein et le dernier plein restent ;
- l'énergie de la résistance ou le compteur d'eau chaude : les entités restent ; l'augmentation du compteur est
  comptée à son retour.

Une coupure plus courte (un redémarrage de Home Assistant, une mise à jour de l'appareil) ne change rien. Chaque
source indisponible depuis 15 minutes est journalisée une fois, avec la cause, et une fois à son retour. Une entité
source qui **n'existe plus** (renommée, son appareil supprimé) lève aussi une réparation dans *Paramètres* >
*Réparations* : reconfigurez le chauffe-eau pour choisir son remplaçant.

## Services

- `smart_hass_water_heater.mark_full` (`config_entry_id`) : le ballon est plein maintenant.
- `smart_hass_water_heater.set_level` (`config_entry_id`, `level_l`) : fixe l'eau chaude restante, en litres à la
  consigne.

## Mise à jour des données

Rien n'est interrogé périodiquement. L'estimation suit **chaque changement d'état** des entités sources, et se
rafraîchit toutes les 5 minutes entre deux (les pertes s'accumulent, le temps depuis le plein avance). La fin de
chauffe est vérifiée 60 s après la chute de la puissance, sans attendre de nouvelle mesure. L'estimation est
enregistrée 30 s après un changement et à l'arrêt de Home Assistant : un redémarrage ne perd rien, et les pertes de
la coupure sont comptées.

## Cas d'usage

- **Chauffer seulement quand il faut** : compléter en heures creuses, ou sur le surplus solaire, seulement sous un
  niveau — au lieu de chauffer toutes les nuits.
- **Planifier la chauffe** : un planificateur d'énergie (par ex. Smart Hass - Energy Optimizer) lit le niveau et
  l'énergie jusqu'au plein pour placer la chauffe aux heures les moins chères et garder assez d'eau chaude pour le
  soir. L'entité *Niveau d'eau chaude* porte, en attributs, tout ce dont il a besoin : le ballon (volume, capacité,
  pertes, températures), l'énergie jusqu'au plein, le temps depuis le plein, la demande et ses seuils, et les entités
  sources — une seule entité à configurer.
- **Anti-légionelle** : chauffer tout le ballon quand le temps depuis le plein dépasse quelques jours.
- **Savoir avant la douche** : un tableau de bord ou une notification quand l'eau chaude baisse.

## Exemples

Les identifiants ci-dessous sont ceux d'un chauffe-eau nommé *Chauffe-eau*, avec Home Assistant en français ; les
vôtres suivent le nom que vous avez donné et la langue de Home Assistant à la création.

Compléter à 2 h seulement sous un demi-ballon (le thermostat coupe au plein ; le relais est coupé une fois plein) :

```yaml
automation:
  - alias: Chauffe-eau - complément heures creuses
    triggers:
      - trigger: time
        at: "02:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.chauffe_eau_niveau_d_eau_chaude
        below: 50
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.chauffe_eau
  - alias: Chauffe-eau - relais coupé au plein
    triggers:
      - trigger: state
        entity_id: sensor.chauffe_eau_dernier_plein
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.chauffe_eau
```

Anti-légionelle, 72 h sans ballon plein :

```yaml
automation:
  - alias: Chauffe-eau - anti-légionelle
    triggers:
      - trigger: numeric_state
        entity_id: sensor.chauffe_eau_temps_depuis_le_plein
        above: 72
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.chauffe_eau
```

Une notification quand il reste moins de 40 L :

```yaml
automation:
  - alias: Chauffe-eau - eau chaude basse
    triggers:
      - trigger: numeric_state
        entity_id: sensor.chauffe_eau_eau_chaude
        below: 40
    actions:
      - action: notify.notify
        data:
          message: "Il reste moins de 40 L d'eau chaude."
```

## Limites connues

- **Une seule température pour tout le ballon** : le niveau est un équivalent en litres à la consigne. Un vrai
  ballon est stratifié (chaud en haut), et un mitigeur allonge l'eau chaude : vous pouvez disposer de plus d'eau
  utile qu'affiché.
- **Paramètres constants** : la température de l'eau froide (environ 8 °C en hiver, 18 °C en été) et les pertes à
  l'arrêt (elles dépendent de la température de la pièce) sont des options fixes. Une erreur sur elles se voit comme
  un saut au plein suivant ; ajustez-les si le saut est toujours dans le même sens.
- **Les pertes s'apprennent sur des cycles sans aucun puisage** : entre deux ballons pleins à 12 h d'écart au moins,
  avec un compteur d'eau chaude qui n'a compté aucun litre, ce qui a été chauffé est ce qui a été perdu. Ces cycles
  arrivent surtout pendant une absence (il faut environ 12 h de pertes seules pour relancer le thermostat) ; le
  capteur *Pertes apprises* donne les pertes des cinq derniers, ses attributs les pertes configurées, celles utilisées
  et le dernier cycle mesuré (même rejeté, hors de ¼ à 4 fois les pertes configurées : une panne). Elles ne remplacent
  les pertes à l'arrêt qu'une fois *Apprendre les pertes* activé.
- **Recalé par le thermostat seulement** : l'estimation est remise à zéro par un ballon plein. Un ballon jamais
  chauffé jusqu'à la coupure du thermostat (relais toujours coupé avant) dérive ; chauffez jusqu'au plein de temps
  en temps, ou appelez `mark_full`.
- **Sans compteur d'eau chaude**, un puisage ne se voit qu'à la fin de chauffe suivante, et la demande n'est
  estimée qu'à partir des pertes.
- **La demande apprend grâce au relais** : ses seuils sont corrigés quand le relais s'allume (qu'il chauffe ou non).
  Le seuil de puisage démarre au premier litre, ce qui penche vers autoriser une chauffe, et monte à mesure que le
  relais allumé montre qu'un petit puisage ne relance pas le thermostat.
- **Résistances seulement** : la puissance d'un chauffe-eau thermodynamique n'est pas la chaleur qu'il fournit (son
  rendement varie), et un chauffe-eau avec sa propre horloge ou un mode éco peut couper sans que le ballon soit plein.
- **Un relais, un ballon** : les ballons en série ou à plusieurs résistances sur des relais séparés ne sont pas pris
  en charge.

## Dépannage

| Symptôme | Que vérifier |
|----------|--------------|
| Le niveau reste *inconnu* | Aucun ballon plein vu : appelez `set_level` avec votre estimation, ou laissez une chauffe aller jusqu'à la coupure du thermostat |
| Le ballon plein n'est jamais vu | Le relais doit rester fermé jusqu'à la coupure du thermostat, et le capteur de puissance doit lire moins de 5 W en moins d'une minute (un capteur publié toutes les quelques minutes est trop lent, voir [Matériel](hardware.md)) |
| Le niveau est *indisponible* | Le relais ou la puissance est indisponible depuis 15 minutes : le journal dit lequel et pourquoi ; *Paramètres* > *Réparations* si l'entité n'existe plus |
| Le niveau baisse trop vite ou trop lentement | Les pertes à l'arrêt et la température de l'eau froide (options) ; l'unité du compteur (L ou m³) et son emplacement (avant tout mitigeur) |
| Un puisage est compté deux fois ou pas du tout | Le total du compteur : un total qui repart de 0 est une remise à zéro, comptée à partir de là ; les impulsions perdues pendant que le compteur était hors ligne ne se retrouvent pas |

Pour signaler un problème, joignez les **diagnostics** (*Paramètres* > *Appareils et services* > *Smart Hass -
Water Heater* > le chauffe-eau > ⋮ > *Télécharger les diagnostics*) : les options, l'estimation, l'état du modèle et
ce que montre chaque entité source.

## Désinstallation

1. *Paramètres* > *Appareils et services* > *Smart Hass - Water Heater* > ⋮ sur chaque chauffe-eau > *Supprimer* :
   ses entités et son estimation enregistrée sont supprimées.
2. Dans HACS, supprimez *Smart Hass - Water Heater*, puis redémarrez Home Assistant.

Mettez d'abord à jour ce qui lit ses entités (automatisations, tableaux de bord, planificateur d'énergie) : elles
deviennent indisponibles.
