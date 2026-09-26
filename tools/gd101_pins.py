"""Programming output commands recovered from 7ab57770 and 7ab76687."""
VOLTAGE_OFF=0xffffffff
SHORT_TO_GROUND=0xfffffffe


def supported_pin(pin,voltage):
    if voltage==VOLTAGE_OFF:return pin in (9,12,13)
    if voltage==SHORT_TO_GROUND:return pin==9
    return pin in (12,13)


def build_pin_control(pin,voltage):
    if not isinstance(voltage,int) or not 0<=voltage<=0xffffffff:
        raise ValueError('Programming voltage must be uint32')
    if not supported_pin(pin,voltage):raise ValueError('Unsupported programming pin/mode')
    wire_value=0 if voltage==VOLTAGE_OFF else voltage&0xffff
    return bytes([1,5,pin])+wire_value.to_bytes(2,'little')
