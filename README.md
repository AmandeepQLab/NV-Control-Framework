\# NV Control Framework



Python-based experimental control software for nitrogen-vacancy (NV) center quantum sensing and imaging experiments.



The framework is designed to provide a modular, scalable, and hardware-independent platform for wide-field NV microscopy and future confocal implementations. The software integrates microwave sources, cameras, pulse generators, and experimental control logic into a unified architecture.



Current development focuses on ODMR (Optically Detected Magnetic Resonance) measurements using an Andor Neo 5.5 camera, Stanford Research Systems SG386 microwave source, and Swabian Pulse Streamer 8/2.



\---



\# Project Goals



The long-term objective of this framework is to support:



\* Wide-field ODMR

\* Rabi measurements

\* Ramsey measurements

\* T1 relaxometry

\* T2 coherence measurements

\* Magnetic field imaging

\* Current density imaging

\* Quantum sensing experiments

\* Future confocal NV microscopy

\* Future lock-in and event-based imaging systems



The architecture is designed so that experiments remain independent of specific hardware implementations.



\---



\# Current Features



\## Implemented



\### Hardware Control



\* SG386 microwave source integration

\* Swabian Pulse Streamer integration

\* Andor Neo 5.5 integration

\* JSON-based hardware configuration



\### ODMR



\* Frequency sweep acquisition

\* MW ON/OFF normalization

\* Real-time data acquisition

\* Live GUI plotting

\* Real hardware testing



\### Software Infrastructure



\* Modular hardware abstraction layer

\* Experiment execution framework

\* Pulse sequence generation

\* Configuration management

\* Hardware test suite



\---



\# Experimental Hardware



\## Microwave Source



Device:



\* Stanford Research Systems SG386



Functions:



\* Frequency control

\* Power control

\* RF enable/disable

\* Frequency sweeps



\---



\## Pulse Generator



Device:



\* Swabian Pulse Streamer 8/2



Functions:



\* Laser control

\* Microwave timing

\* Camera synchronization

\* Pulse sequencing



Current Channel Mapping:



| Signal     | Channel |

| ---------- | ------- |

| greenLaser | 1       |

| MW         | 2       |

| flipMF     | 3       |

| detector   | 5       |

| detector2  | 6       |



Analog Channels:



| Signal   | Channel |

| -------- | ------- |

| aomPower | 1       |



\---



\## Cameras



\### Current



\* Andor Neo 5.5 sCMOS

\* Andor SDK3



Verified:



\* Camera discovery

\* Camera connection

\* Frame acquisition

\* Exposure control



\---



\### Planned



\#### Event Camera



Highest-priority future camera integration.



Intended applications:



\* High-speed ODMR

\* Event-based sensing

\* Fast magnetic imaging



\---



\#### C4 Lock-In Camera



Second-priority future camera integration.



Intended applications:



\* Lock-in ODMR

\* Synchronous imaging

\* Noise suppression

\* High-sensitivity measurements



\---



\## Positioning Hardware



Planned support:



\* SmarAct XYZ stages



\---



\# Software Architecture



The framework follows a layered architecture.



Experiment logic should not directly communicate with hardware devices.



Hardware abstraction layers isolate experiment code from device-specific implementations.



```text

GUI

&#x20;│

&#x20;▼

Controller

&#x20;│

&#x20;▼

Experiment

&#x20;│

&#x20;▼

Hardware Interface

&#x20;│

&#x20;▼

Device Driver

```



Repository structure:



```text

hardware/

│

├── andor\_camera.py

├── srs\_mw.py

├── swabian\_driver.py

├── sim\_camera.py



experiments/

│

├── odmr\_experiment.py



controller/

│

├── experiment\_runner.py



sequencing/

│

├── pulse\_sequence.py



gui/

│

├── main\_window.py

├── odmr\_panel.py

├── odmr\_worker.py



config/

│

├── setupInfo.json



tests/

│

├── test\_odmr.py

├── test\_pulse\_streamer.py

├── test\_swabian\_compile.py

├── test\_swabian\_output.py

├── test\_andor\_detect.py

├── test\_andor\_class.py

├── test\_real\_odmr\_point.py

```



\---



\# Current ODMR Workflow



For each microwave frequency:



1\. Acquire reference image with microwave OFF

2\. Acquire measurement image with microwave ON

3\. Compute normalized ODMR value



```text

ODMR = I\_on / I\_off

```



Where:



\* I\_on = Microwave ON image

\* I\_off = Microwave OFF image



Repeating this process over frequency generates the ODMR spectrum.



\---



\# Installation



\## Clone Repository



```bash

git clone git@github.com:AmandeepQLab/NV-Control-Framework.git

cd NV-Control-Framework

```



\---



\## Create Virtual Environment



```bash

python -m venv .venv

```



\---



\## Activate Environment



Windows:



```bash

.venv\\Scripts\\activate

```



Git Bash:



```bash

source .venv/Scripts/activate

```



\---



\## Install Dependencies



```bash

pip install -r requirements.txt

```



\---



\# Running the Software



Launch the main application:



```bash

python main.py

```



\---



\# Configuration



Hardware configuration is centralized in:



```text

config/setupInfo.json

```



The configuration file defines:



\* Hardware types

\* Device addresses

\* Channel mappings

\* Camera settings

\* Pulse streamer configuration



No experiment code should contain hardcoded hardware settings.



\---



\# Documentation



Detailed architecture and development information:



```text

PROJECT\_CONTEXT.md

```



This document contains:



\* Hardware architecture

\* Development philosophy

\* Design decisions

\* Future roadmap

\* Guidance for AI coding assistants



\---



\# Development Priorities



\## Priority 1



\* Event Camera Integration

\* Event Camera GUI support

\* Event Camera ODMR workflow



\## Priority 2



\* C4 Lock-In Camera Integration

\* Lock-In ODMR workflow

\* Lock-In GUI support



\## Priority 3



\* Rabi measurements

\* Ramsey measurements

\* T1 relaxometry

\* T2 coherence measurements



\## Priority 4



\* Confocal NV support

\* Advanced automation

\* Multi-user configurations



\---



\# Development Workflow



Stable code:



```text

main

```



Active development:



```text

dev

```



Recommended workflow:



```bash

git checkout dev

git add .

git commit -m "Describe change"

git push

```



Merge into main only after testing.



\---



\# Screenshots



Future additions:



\* Main GUI

\* Live camera view

\* ODMR acquisition window

\* Event camera interface

\* C4 lock-in camera interface



\---



\# Planned GitHub Issues



\* Event Camera Integration

\* C4 Lock-In Camera Integration

\* Rabi Experiment

\* Ramsey Experiment

\* T1 Experiment

\* T2 Experiment

\* SmarAct Integration

\* Data Management System



\---



\# Project Status



Active development.



Current hardware operation has been verified with:



\* SG386 Microwave Source

\* Swabian Pulse Streamer 8/2

\* Andor Neo 5.5 Camera



Real ODMR acquisition has been successfully demonstrated.



The framework serves as the foundation for future NV-center sensing and imaging experiments.



