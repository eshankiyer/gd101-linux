import unittest
from gd101_isotp import ISOTPTransmitDone,ISOTPReassembler
from gd101_receiver import PacketReceiver
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
import test_gd101_periodic_isotp as helpers

class IndicationOverflowTests(unittest.TestCase):
    def session(self):
        d=helpers.PeriodicISOTests().session()
        d.receiver=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({},timeout=1),limit=1)
        d.receive_packet.side_effect=None;d.receive_packet.return_value=bytes.fromhex('43000000007856341200')
        return d

    def test_successful_send_stays_successful_when_indication_queue_full(self):
        d=self.session();prior=ISOTPTransmitDone(0x7e0,False,None,1)
        d.receiver.publish_isotp_completion(prior)
        self.assertEqual(d.write_isotp_message(0,b'\x3e\x80'),0x12345678)
        self.assertFalse(d.tx_uncertain);self.assertEqual(d.send_packet.call_count,1)
        self.assertEqual(d.receiver.indication_overflow_total,1)
        p=LifecycleProvider();p.channel=1;p.protocol=6;p.session=d
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');m.ProtocolID=99
        count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),18)
        self.assertEqual(count[0],0);self.assertEqual(m.ProtocolID,99)
        count[0]=1
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),0)
        self.assertEqual(m.Timestamp,1)

    def test_loopback_loss_does_not_repeat_acknowledged_send(self):
        d=self.session()
        config=bytearray(d.flow_config);config[4]=1;d.flow_config=bytes(config)
        self.assertEqual(d.write_isotp_message(0,b'\x3e\x80'),0x12345678)
        self.assertFalse(d.tx_uncertain)
        self.assertEqual(d.send_packet.call_count,1)
        self.assertEqual(d.receiver.indication_overflow_total,1)
        with self.assertRaises(BufferError):d.receiver.read_message(0)
        self.assertEqual(d.receiver.read_message(0),ISOTPTransmitDone(0x7e0,False,None,0x12345678))
        with self.assertRaises(TimeoutError):d.receiver.read_message(0)

    def test_clear_resets_pending_overflow_but_retains_total(self):
        d=self.session();r=d.receiver
        for i in range(4):r.publish_isotp_completion(ISOTPTransmitDone(0x7e0,False,None,i))
        self.assertEqual(r.indication_overflow,3)
        r.clear_receive(5)
        self.assertEqual(r.indication_overflow,0);self.assertEqual(r.indication_overflow_total,3)
        with self.assertRaises(TimeoutError):r.read_message(0)

if __name__=='__main__':unittest.main()
