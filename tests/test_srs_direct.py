import pyvisa

ip = "132.64.56.186"

resource = f"TCPIP0::{ip}::inst0::INSTR"

print("\nConnecting to:\n")
print(resource)

rm = pyvisa.ResourceManager()

try:

    inst = rm.open_resource(resource)

    inst.timeout = 5000

    print("\nConnected successfully.\n")

    response = inst.query("*IDN?")

    print("Instrument ID:")
    print(response)

    inst.close()

except Exception as e:

    print("\nFAILED:\n")
    print(e)
