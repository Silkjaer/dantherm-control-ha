# Sensorer og alarmer

Dette er den korrekte betydning af Dantherm Control-entiteterne for HCH5 MK1 med
HAC1. Integrationen aflæser kun observeret RS485-trafik; den sender ingen
Modbus-forespørgsler eller kommandoer.

## Temperaturer og indeklima

| Entitet | Betydning og kilde |
| --- | --- |
| Udelufttemperatur (T1) | Anlæggets egen føler for luft ind udefra. |
| Indblæsningstemperatur (T2) | Luft sendt ind i huset. |
| Udsugningstemperatur (T3) | Luft fra huset til anlægget. |
| Afkasttemperatur (T4) | Luft, der forlader anlægget efter varmegenvinding. |
| Hustemperatur (T5) | HRC2-rumføleren fra HAC1-snapshot 180-209. Uden HAC1 læses den fra HCP4's skrivning af T1-T5 til register 180-184. |
| Relativ luftfugtighed | Beregnet fra HRC2-rådata med rumtemperaturkompensation. Det er ikke PC Tools byte-skalerede værdi og er ikke laboratoriekalibreret. |
| CO2 | CO2-koncentration fra HAC1, i ppm. |
| Luftkvalitet | Afledt af CO2: god under 800 ppm, moderat 800-1200 ppm og dårlig over 1200 ppm. |

T1-T5 fra et HAC1-snapshot publiceres samlet, så afledte værdier ikke blander
temperaturer fra forskellige måletidspunkter.

## Ventilation og varmegenvinding

| Entitet | Betydning og kilde |
| --- | --- |
| Udsugnings-/indblæsningsventilator hastighed | Observerede omdrejninger, rpm. |
| Udsugnings-/indblæsningsventilator styring | Senest observerede HCP4/HRC2-kommando, procent; ikke målt luftmængde. |
| Ventilatorbalance styring / omdrejninger | Afledt forskel mellem ventilatorerne, ikke kalibreret luftmængde. |
| Bypass aktiv | Sand når den observerede bypass-råværdi er 255. |
| Temperaturvirkningsgrad | `(udsugning - afkast)/(udsugning - ude)`. Udelades ved aktiv bypass eller for lille temperaturspænd. |
| Delta indblæsning/udsugning | Indblæsning minus udsugning. |
| Status, udvikling, reference og fald for varmegenvinding | Afledte kvalitetsmål: god fra 85 %, acceptabel fra 70 %; udvikling advarer ved fald på 7,5 og 12,5 procentpoint fra en langsomt lært lokal reference. |
| Driftstilstand og ventilationstrin | Afledt af observerede HCP4/HRC2-kommandoer. Automatisk og ugeprogram kan ikke altid skelnes. |
| Pejsefunktion, standby og natdrift | Observerede specialtilstande fra kommando- og ventilatortrafik. |

Ventilatorstyring, balance, driftstilstand, ventilationstrin, pejsefunktion og
standby kommer kun fra HCP4/HRC2-panelets skrivninger på bussen. Når Pi-controlleren
er sat op og er master, og der ikke sidder et panel på bussen, oprettes disse
entiteter først, når den første værdi er observeret, i stedet for at stå som
utilgængelige. Pi'ens egne værdier findes i stedet som Faktisk udsugning,
Faktisk indblæsning og Effektivt ventilationsniveau.

## Eftervarme, vand og filter

| Entitet | Betydning og kilde |
| --- | --- |
| Indblæsning, rum og udsugning setpoint | Observerede HAC1-termostatindstillinger. `OFF` betyder deaktiveret. Sidste observerede setpoint bevares lokalt efter genstart. |
| Luft efter varmeflade (T2AH) | Lufttemperatur efter eftervarmefladen. |
| Eftervarme antifrost (TFAH) | Vand-/frosttemperatur ved varmefladen; det er ikke luft før varmefladen. |
| Luftdelta over varmeflade | Luft efter varmeflade minus indblæsning, kun fra samlet snapshot. |
| Ventilåbning eftervarme | Observeret kommando 0-100; ikke en bekræftet fysisk ventilposition. |
| Fremløb, retur, vand delta-T, varmeoverførsel og temperaturfølere forbundet | Valgfri DS18B20-forvarmeudvidelse. Fejl her påvirker ikke RS485-delen. |
| Filterinterval, dage, restlevetid, status, alarm, seneste og antal skift | Lokal filtercyklus synkroniseret med observeret HCP4-data. Nulstilling skriver aldrig til bussen. |

## Raspberry Pi

Kun til stede når de valgfrie vandforvarmefølere er aktiveret i integrationens
indstillinger (uanset om DS18B20-følerne faktisk er tilsluttet). Ligger på en
separat enhed **Raspberry Pi** og siger noget om Pi'ens egen sundhed, ikke om
Dantherm-anlægget.

| Entitet | Betydning og kilde |
| --- | --- |
| CPU-temperatur, kernespænding, belastning (1 min), hukommelse og disk brugt | Læst direkte fra `/sys`, `/proc` og `vcgencmd` på Pi'en. |
| Model, kernel-version | Statisk hardware-/OS-identifikation, deaktiveret som standard. |
| Underspænding, neddroslet, frekvensbegrænset, blød temperaturgrænse (nu / siden opstart) | `vcgencmd get_throttled`-bitmasken. "Nu" er den aktuelle tilstand; "siden opstart" forbliver sand indtil næste genstart, selv efter tilstanden er normaliseret igen. |

## Drift og rådiagnostik

HAC1-forbindelse, RS485-bustrafik og telegrammer pr. minut er
forbindelsesdiagnostik. Manglende gateway eller HAC1 i 15 minutter giver en
Home Assistant Repairs-advarsel.

Varmegenvinding råværdi, bypass råstatus, statuskode, eftervarme råstatus og
betjeningskommando råværdi er kun diagnostik. De må ikke oversættes til
Danfoss-alarmkoder; især er `status_code` ikke en filteralarm.

## Afledte alarmer

De følgende problem-entiteter ligger på enheden **Alarmer**. De er afledte
målinger, ikke påståede originale HRC2 E-koder.

| Alarm | Betingelse |
| --- | --- |
| Udsugningsventilator kører ikke / Indblæsningsventilator kører ikke | Kommando mindst 20 % og observeret hastighed under 100 rpm. |
| Ude-, indblæsnings-, udsugnings-, afkast- eller rumtemperaturføler fejl | En komplet HAC1-temperaturprøve har ingen gyldig værdi for føleren. Opstart og delvise telegrammer alarmerer ikke. |
| Udetemperatur under -13 °C | T1 er under -13 °C. |
| Indblæsning under 5 °C | T2 er under 5 °C. |
| Brandtemperatur over 70 °C | T3 er over 70 °C. |

E3 (bypass), E9 (fugtighedsføler), E13 (HRC2-radio) og E14 (ekstern
brandtermostat) kan ikke afgøres sikkert af den passive RS485-trafik. E13 er
testet aktivt: den vises på HRC2, men gav hverken en ny Modbus-adresse eller en
ændring i rå statuskode. De er derfor bevidst ikke falske alarm-entiteter.
