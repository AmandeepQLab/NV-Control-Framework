\# NV Control Framework



\## Overview



Python-based control software for NV-center experiments.



The framework is designed for wide-field NV microscopy and is structured to support future confocal implementations.



Current focus is ODMR acquisition and hardware integration.



\---



\# Hardware



\## Microwave Source



Device: SRS SG386



Control:



\* TCP/IP VISA



Functions:



\* Frequency sweep

\* Power control

\* Output enable/disable



\---



\## Pulse Generator



Device: Swabian Pulse Streamer 8/2



Functions:



\* Laser triggering

\* Microwave triggering

\* Camera triggering

\* Experiment timing



Channel Mapping:



| Signal     | Channel |

| ---------- | ------- |

| greenLaser | 1       |

| MW         | 2       |

| flipMF     | 3       |

| detector   | 5       |

| detector2  | 6       |



Analog:



| Signal   | Channel |

| -------- | ------- |

| aomPower | 1       |



\---



\## Camera



Primary:



\* Andor Neo 5.5



Supported:



\* SDK3



Future:



\* Event Camera



\---



\## Stage



SmarAct XYZ



\---



\# Current Acquisition Method



For each microwave frequency:



1\. MW OFF image acquired

2\. MW ON image acquired

3\. Normalize



ODMR value:



y = I\_on / I\_off



\---



\# Repository Structure



hardware/



\* andor\_camera.py

\* srs\_mw.py

\* swabian\_driver.py



experiments/



\* odmr\_experiment.py



controller/



\* experiment\_runner.py



sequencing/



\* pulse\_sequence.py



gui/



\* main\_window.py

\* odmr\_panel.py



config/



\* setupInfo.json



tests/



\* hardware and integration tests



\---



\# Current Status



Working:



\* SG386 connection

\* Pulse Streamer connection

\* Andor connection

\* ODMR acquisition

\* GUI launch

\* Real hardware testing



Verified:



\* Single ODMR point acquired

\* MW ON/OFF normalization functioning



\---



\# Planned Experiments



1\. ODMR

2\. Rabi

3\. Ramsey

4\. T1

5\. T2



\---



\# Development Philosophy



Hardware abstraction layer should isolate experiment code from device-specific implementations.



Experiments should only communicate through hardware interfaces.



Configuration should be centralized in setupInfo.json.



\---



\# Notes for AI Assistants



Before modifying code:



1\. Preserve existing hardware interfaces.

2\. Preserve JSON configuration compatibility.

3\. Avoid hardcoded channel numbers.

4\. Keep experiment classes independent.

5\. Maintain compatibility with SG386 and Pulse Streamer.



