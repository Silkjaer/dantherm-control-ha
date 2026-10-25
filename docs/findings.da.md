# Fund på Dantherm HCH5 MK1-bussen

## Det observerede system

På den undersøgte Dantherm HCH5 MK1 med HAC1 findes der allerede en aktiv Modbus RTU-master. Masteren sender løbende forespørgsler, hvorefter ventilationsanlægget svarer som slave. Trafikken indeholder blandt andet temperaturer, CO₂, ventilatorhastigheder, driftstilstand, ventilationstrin og bypass-status.

Integrationens registerfortolkning er baseret på observationer fra denne konkrete installation. Råværdier, som endnu ikke er sikkert identificeret, markeres som råværdier og er som udgangspunkt deaktiveret i Home Assistant.

## Hvorfor integrationen kun læser

Den eksisterende forbindelse drives som en single-master Modbus RTU-bus. Det betyder ikke, at en anden enhed fysisk er forhindret i at sende. En ekstra enhed kan teknisk sende en gyldig kommando, og anlægget kan nå at acceptere den.

Den eksisterende master fortsætter imidlertid sin normale styringscyklus og skriver efterfølgende sine egne værdier igen. En værdi sendt af Home Assistant vil derfor typisk kun være midlertidig og derefter blive overskrevet af den oprindelige master.

Hvis Home Assistant eller en RS485-til-Ethernet-adapter også begyndte at sende forespørgsler, ville der desuden være to uafhængige sendere på samme ledningspar uden koordinering. Telegrammer kan kollidere og give CRC-fejl, timeouts og ustabil kommunikation. Der ville også være risiko for at forstyrre den eksisterende styring eller skabe utilsigtet adfærd.

Vi behøver ikke at tage den risiko. Den eksisterende master spørger allerede efter de relevante data, og svarene kan aflæses passivt. Løsningen er derfor bevidst bygget som en sniffer:

1. RS485-gatewayen lytter til trafikken i begge retninger.
2. De observerede bytes videresendes uændret som en rå TCP-strøm.
3. Home Assistant-integrationen modtager og afkoder strømmen.
4. Integrationen sender ingen Modbus-forespørgsler, kvitteringer eller kommandoer.

Read-only er derfor et bevidst design- og sikkerhedsvalg: skrivning ville være upålidelig, kortvarig og potentielt forstyrrende. Det er ikke, fordi kommandoer er fysisk umulige at sende.

## Krav til en senere RS485-til-Ethernet-adapter

En senere adapter skal kunne levere en transparent rå TCP-strøm med de samme serielle indstillinger (`19200 8E1`). Modbus TCP-konvertering, automatisk polling, MQTT-polling, heartbeat-data og andre funktioner, der kan sende bytes på RS485-bussen, skal være slået fra.

En almindelig transparent adapter kan fysisk sende, hvis en TCP-klient skriver til den. Den skal derfor konfigureres uden automatisk seriel trafik, og der må ikke forbindes anden software, som sender data gennem den. Dantherm Control skriver aldrig til TCP-forbindelsen og fungerer alene som modtager.

Adapterens IP-adresse og TCP-port bestemmes af brugeren i adapterens opsætning. Integrationen er ikke bundet til en bestemt computer, IP-adresse eller adapterport.

M-Bus er en anden elektrisk og protokolmæssig standard end RS485 og kan ikke anvendes som erstatning.

## Uofficiel løsning

Dette community-projekt er ikke udviklet, leveret, bestilt, godkendt, certificeret eller supporteret af Dantherm Group. Dantherm Group har ingen tilknytning til projektet. Navnet Dantherm bruges alene til at identificere kompatibelt udstyr, og alle varemærker tilhører deres respektive ejere.
