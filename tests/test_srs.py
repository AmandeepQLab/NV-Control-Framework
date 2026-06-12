import pyvisa

rm = pyvisa.ResourceManager()

resources = rm.list_resources()

print("\nDetected VISA Resources:\n")

for r in resources:

    print(f"\nConnecting to: {r}")

    try:

        inst = rm.open_resource(r)

        inst.timeout = 2000

        response = inst.query("*IDN?")

        print("IDN:", response)

        inst.close()

    except Exception as e:

        print("FAILED:", e)
