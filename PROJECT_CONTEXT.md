\# NV Control Framework



\## Project Purpose



NV Control Framework is a Python-based experimental control platform for nitrogen-vacancy (NV) center measurements in diamond.



The long-term goal is to provide a modular, hardware-independent framework for:



\* ODMR (Optically Detected Magnetic Resonance)

\* Rabi measurements

\* Ramsey measurements

\* T1 relaxometry

\* T2 coherence measurements

\* Wide-field magnetic imaging

\* Future confocal implementations



The framework is designed around hardware abstraction layers so that experiments remain independent of specific devices.



\---



\# Current Experimental Setup



\## Microwave Source



Device:



\* Stanford Research Systems SG386



Control:



\* TCP/IP VISA communication



Functions:



\* Frequency control

\* Power control

\* RF output enable/disable

\* Frequency sweep



Status:



\* Connected and tested successfully



\---



\## Pulse Generator



Device:



\* Swabian Pulse Streamer 8/2



Communication:



\* JSON-RPC



Functions:



\* Laser timing

\* Microwave timing

\* Camera synchronization

\* Experimental pulse sequencing



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



Status:



\* Connected and tested successfully



\---



\# Camera Architecture



The framework is designed to support multiple camera backends through a common camera interface.



Current implementation:



Primary Device:



\* Andor Neo 5.5 sCMOS



SDK:



\* Andor SDK3



Status:



\* Connected

\* Tested

\* Used for current ODMR acquisition



Current Features Verified:



\* Camera discovery

\* Camera connection

\* Frame acquisition

\* Exposure control



Reported Frame Size:



\* 2160 × 2560 pixels



\---



\# Future Camera Support



The camera abstraction layer should allow multiple camera implementations without requiring modifications to experiment logic.



Planned camera backends:



1\. Andor Neo 5.5



&#x20;  \* Current reference implementation



2\. Event Camera



&#x20;  \* Highest priority future camera integration

&#x20;  \* Intended for high-speed ODMR and event-based sensing

&#x20;  \* Should reuse existing experiment architecture

&#x20;  \* Camera-specific processing should remain isolated within the camera driver layer



3\. C4 Lock-In Camera



&#x20;  \* Second highest priority future camera integration

&#x20;  \* Intended for lock-in detection and synchronous imaging

&#x20;  \* Integration should follow the same abstraction model used for Andor and Event Camera

&#x20;  \* Experiment code should remain independent of lock-in implementation details



Future camera support should be selected through configuration rather than experiment-specific code.



All cameras should expose a common interface wherever possible.



\---



\# Camera Development Roadmap



Priority 1



\* Event Camera integration

\* Event camera acquisition interface

\* Event camera GUI support

\* Event camera ODMR workflow



Priority 2



\* C4 Lock-In Camera integration

\* Lock-in acquisition interface

\* Lock-in imaging workflow

\* Lock-in GUI support



Priority 3



\* Additional camera backends

\* Unified camera configuration management

\* Camera capability discovery



\---



\# Notes For AI Coding Assistants



Camera integrations must follow these rules:



1\. Do not modify experiment logic to support a specific camera.



2\. Implement camera-specific functionality inside dedicated hardware drivers.



3\. Maintain a common camera API whenever possible.



4\. New camera support should be selectable through setupInfo.json.



5\. Preserve compatibility with the existing Andor implementation.



6\. Event Camera development currently has higher priority than C4 Lock-In Camera development.



7\. C4 Lock-In Camera development has higher priority than additional hardware integrations.

\---



\## Positioning



Device:



\* SmarAct XYZ stages



Status:



\* Planned integration



\---



\# Software Architecture



The framework follows a layered architecture.



High-level experiment logic should never directly access hardware.



Experiments communicate through hardware abstraction classes.



Architecture:



hardware/



\* Device-specific drivers



experiments/



\* ODMR

\* Rabi

\* Ramsey

\* T1

\* T2



controller/



\* Experiment execution layer



sequencing/



\* Pulse sequence generation



gui/



\* User interface



config/



\* Hardware configuration



tests/



\* Hardware and integration tests



\---



\# Configuration Philosophy



All hardware configuration should be centralized in:



config/setupInfo.json



No experiment code should contain hardcoded:



\* IP addresses

\* Channel numbers

\* Hardware names

\* Device-specific settings



The JSON configuration file should be the single source of truth.



\---



\# Current ODMR Implementation



Measurement Method:



For each microwave frequency:



1\. Acquire reference image with microwave OFF

2\. Acquire measurement image with microwave ON

3\. Normalize



Current normalization:



ODMR = I\_on / I\_off



where:



I\_on:



\* Microwave ON image



I\_off:



\* Microwave OFF image



The normalized value becomes one ODMR point.



Repeating over frequency creates the ODMR spectrum.



\---



\# Verified Hardware Functionality



Successfully Tested:



\* SG386 communication

\* SG386 frequency control

\* Swabian communication

\* Pulse sequence compilation

\* Pulse sequence streaming

\* Andor camera connection

\* Andor frame acquisition

\* Real ODMR point acquisition

\* GUI launch

\* Live ODMR plotting



\---



\# Existing Modules



Known modules include:



hardware/



\* andor\_camera.py

\* srs\_mw.py

\* swabian\_driver.py

\* sim\_camera.py



experiments/



\* odmr\_experiment.py



controller/



\* experiment\_runner.py



sequencing/



\* pulse\_sequence.py



gui/



\* main\_window.py

\* odmr\_panel.py

\* odmr\_worker.py



config/



\* setupInfo.json



tests/



\* test\_odmr.py

\* test\_pulse\_streamer.py

\* test\_swabian\_compile.py

\* test\_swabian\_output.py

\* test\_andor\_detect.py

\* test\_andor\_class.py

\* test\_real\_odmr\_point.py



\---



\# GUI Requirements



The GUI should support:



Camera:



\* Live view

\* Exposure control

\* ROI selection

\* Binning



ODMR:



\* Frequency sweep

\* Real-time plotting

\* Averaging

\* Repeats

\* Save data



Future:



\* Rabi tab

\* Ramsey tab

\* T1 tab

\* T2 tab



\---



\# Development Principles



1\. Preserve hardware abstraction.



Experiment code should not depend on vendor APIs.



2\. Preserve JSON configuration.



Avoid hardcoded channel numbers.



3\. Keep experiment classes independent.



Each experiment should be self-contained.



4\. Maintain backward compatibility.



Avoid breaking existing ODMR workflows.



5\. Prioritize readability over clever implementations.



The framework is intended for long-term laboratory use.



\---



\# Known Future Work



High Priority



\* Rabi experiment

\* Ramsey experiment

\* T1 experiment

\* T2 experiment



Medium Priority



\* Event camera integration

\* Data management system

\* Experiment presets

\* Automated saving



Lower Priority



\* Confocal support

\* Multi-user configuration profiles



\---



\# Notes For AI Coding Assistants



Before modifying code:



1\. Inspect setupInfo.json first.



2\. Preserve existing hardware interfaces.



3\. Do not hardcode pulse streamer channels.



4\. Do not hardcode SG386 parameters.



5\. Reuse existing architecture whenever possible.



6\. Extend current modules before creating new frameworks.



7\. Prefer minimal modifications over large refactors.



8\. Maintain compatibility with:



&#x20;  \* SG386

&#x20;  \* Swabian Pulse Streamer 8/2

&#x20;  \* Andor Neo 5.5



9\. Assume ODMR is the reference implementation for future experiments.



10\. New experiments should follow the same pattern:



GUI → Controller → Experiment → Hardware → Device



\---



\# Current Repository Status



Repository contains a working foundation for:



\* Hardware control

\* ODMR acquisition

\* GUI operation

\* Real hardware testing



Future development should build on the existing architecture rather than replacing it.



