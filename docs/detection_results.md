# Detection results: ColdTrust against a plain threshold alarm

Both systems receive exactly the same simulated messages (10 scenarios from `sim/sim.py`, deterministic, no AI calls). The baseline is a plain threshold alarm: it alarms at once when a reading leaves the hold's limits (2-8 C) or the light shows the door open (>= 30%), accepts every message, and knows nothing about context. That is the fastest a threshold alarm can be, so it is a fair, favourable baseline.

## Summary

| Measure | Plain threshold alarm | ColdTrust |
|---|---|---|
| False alarms (normal driving + a door opened at an authorised dock) | 1 | 0 |
| Data attacks recognised (replay, spoofed flat temperature, contradicting thermometer) | 0 of 3 | 3 of 3 |
| Incident scenarios where cargo was already held when the first alert went out | 0 of 5 | 5 of 5 |

| Slow failure: ColdTrust alerted earlier by | compressor_fail: 11:00 (min:s) |
|---|---|

## Per scenario

| Scenario | What happens | Alarm (threshold) | Alert (ColdTrust) | ColdTrust says | Packages held at first alert | Untrusted data caught |
|---|---|---|---|---|---|---|
| normal | Six quiet minutes on the road | - (0 batch(es)) | - (0 alert(s)) | - | 0 of 10 | - |
| door_ajar | Door opens while moving, temperature climbs | 3:25 (5 batch(es)) | 3:25 (2 alert(s)) | DOOR_AJAR | 10 of 10 | - |
| theft | Door opens, person seen in camera, outside any authorised stop | 3:25 (1 batch(es)) | 3:25 (1 alert(s)) | THEFT_SUSPECTED | 10 of 10 | - |
| authorised_stop | Door opens at an authorised dock (should NOT quarantine) | 2:15 (1 batch(es)) | - (0 alert(s)) | - | 0 of 10 | - |
| compressor_fail | Slow temperature rise over 45 virtual minutes; budgets run out | 19:00 (27 batch(es)) | 8:00 (5 alert(s)) | THERMAL_EXCURSION | 10 of 10 | - |
| freeze | Sudden drop from 4 C to 0 C (DHT11 floor) | 3:10 (1 batch(es)) | 3:10 (1 alert(s)) | FREEZE_RISK | 10 of 10 | - |
| replay | Attacker resends a captured payload | - (0 batch(es)) | 3:10 (1 alert(s)) | REJECTED_REPLAY_SEQUENCE | 0 of 10 | 3:10 |
| spoof | Door open 7 min yet temperature perfectly flat | 2:00 (8 batch(es)) | 2:00 (2 alert(s)) | DOOR_AJAR | 10 of 10 | 7:00 |
| sensor_disagree | Second thermometer disagrees with the DHT11 (one sensor faked or failing) | - (0 batch(es)) | 4:00 (1 alert(s)) | DATA_TRUST | 0 of 10 | 4:00 |
| stopped_theft | Route active, truck stops mid-route at 0 km/h and the door opens (theft rule) | 2:35 (1 batch(es)) | 2:35 (1 alert(s)) | THEFT_SUSPECTED | 10 of 10 | - |

Times are minutes:seconds from the start of the simulated scenario, when the system first has the data.

## How to read this

- **False alarms.** A door opened at an authorised dock is routine. A threshold alarm cannot tell, so it fires at every dock stop; ColdTrust checks the zone and the speed first and treats it as handling.
- **Earlier on a slow failure.** ColdTrust judges each product against its own limits (for example 0-4 C for fish, 2-6 C for dairy), while the baseline uses one hold limit. In a slowly warming hold the most sensitive product leaves its range first, so the alert comes before the single hold alarm.
- **Not faster on a door event.** When the light changes, both systems know at the same moment. The difference is what happens next: ColdTrust holds the cargo and names the situation (theft, door ajar), the alarm only rings.
- **Data attacks.** A replayed or forged message produces no threshold breach, so a plain alarm never sees it. ColdTrust rejects the replay and flags the flat-temperature and contradicting-sensor cases. In the spoof scenario the threshold alarm does ring for the open door, but it never notices that the temperature is fake; ColdTrust says so after the door has been open for 5 minutes with no temperature response.
- **Cargo held.** A threshold alarm is assumed to hold no cargo: it rings and a person must act. ColdTrust's rules hold or quarantine the packages at the moment of the alert, and an admin can release them.
- **Limits.** These are 10 simulated scenarios built by the same author as the rules, so they show what the rules do on known cases, not how often real incidents occur. The baseline is deliberately generous (instant alarm, no delay). The second-thermometer case needs a sensor that the prototype board does not yet have.
