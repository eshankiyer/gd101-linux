import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider
from gd101_driver import GD101Error
from gd101_can import build_can_open_payload,default_can_descriptor,CANFilters
from gd101_receiver import PacketReceiver
from test_gd101_can_transmit import driver

class RateChangeTests(unittest.TestCase):
    def test_timing_is_stored_then_sent_on_rate_change(self):
        d=self.session()
        d.configure_can_bitrate(500000,timing=(75,20))
        d.exchange.assert_not_called()
        self.assertEqual(d.can_timing,(75,20))
        d.configure_can_bitrate(250000)
        descriptor=bytearray(default_can_descriptor(250000));descriptor[8:10]=bytes([75,20])
        d.exchange.assert_called_once_with(build_can_open_payload(descriptor),b'\x41\0\0',timeout=2)
        d.exchange.side_effect=TimeoutError('lost')
        with self.assertRaises(TimeoutError):d.configure_can_bitrate(125000,timing=(90,10))
        self.assertEqual(d.can_timing,(75,20));self.assertEqual(d.can_bitrate,250000)

    def test_provider_timing_roundtrip_and_failed_commit(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,5,0,500000)
        self.assertEqual(p.configure_timing(c,parameters=[23,24]),(0,[80,15]))
        self.assertEqual(p.configure_timing(c,pairs=[(23,101),(23,331),(24,276)])[0],0)
        p.session.configure_can_bitrate.assert_called_once_with(500000,loopback=False,timing=(75,20))
        self.assertEqual(p.configure_timing(c,parameters=[1,24,23,3]),(0,[500000,20,75,0]))
        p.session.configure_can_bitrate.side_effect=TimeoutError('lost')
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(23,90)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[1,23,24]),(0,[500000,75,20]))

    def session(self):
        d=driver([]);d.can_bitrate=500000;d.can_loopback=False
        d.exchange=Mock(return_value=b'\x41\0\0\0')
        filters=CANFilters();filters.add(1,bytes(4),bytes(4))
        d.receiver=PacketReceiver(None,bytes(32),can_filters=filters)
        return d

    def test_native_descriptor_and_filter_preservation(self):
        d=self.session();reader=d.receiver;filters=reader.can_filters
        for rate in (125000,200000,250000,500000,1000000):
            d.configure_can_bitrate(rate,loopback=True)
            self.assertEqual(d.can_bitrate,rate);self.assertTrue(d.can_loopback)
            d.exchange.assert_called_with(build_can_open_payload(default_can_descriptor(rate)),b'\x41\0\0',timeout=2)
            self.assertIs(d.receiver,reader);self.assertIs(reader.can_filters,filters)
            self.assertFalse(d.tx_uncertain)
        d.exchange.reset_mock();d.configure_can_bitrate(1000000,loopback=False)
        d.exchange.assert_not_called();self.assertFalse(d.can_loopback)

    def test_failure_inhibits_retry_and_transmit(self):
        for failure in (TimeoutError('lost ACK'),b'\x41\0\0\x07',b'\x41\0\0'):
            d=self.session()
            if isinstance(failure,Exception):d.exchange.side_effect=failure
            else:d.exchange.return_value=failure
            with self.assertRaises(Exception):d.configure_can_bitrate(250000,loopback=True)
            self.assertEqual(d.can_bitrate,500000);self.assertFalse(d.can_loopback)
            self.assertTrue(d.tx_uncertain)
            with self.assertRaises(GD101Error):d.configure_can_bitrate(250000)
            with self.assertRaises(GD101Error):d.write_can_frame(1,b'')
            d.exchange.assert_called_once();d.send_packet.assert_not_called()

    def test_rate_change_waits_for_active_transmit(self):
        import threading
        d=self.session();sending=threading.Event();release=threading.Event()
        attempting=threading.Event();changed=threading.Event();errors=[]
        def ack(timeout):
            sending.set()
            if not release.wait(2):raise TimeoutError('test did not release ACK')
            return bytes.fromhex('43000000000100000000')
        d.receive_packet.side_effect=ack
        def send():
            try:d.write_can_frame(0x7df,b'abc')
            except Exception as error:errors.append(error)
        def change():
            attempting.set()
            try:d.configure_can_bitrate(250000)
            except Exception as error:errors.append(error)
            finally:changed.set()
        first=threading.Thread(target=send);second=threading.Thread(target=change)
        first.start()
        try:
            self.assertTrue(sending.wait(1));second.start()
            self.assertTrue(attempting.wait(1))
            self.assertFalse(changed.wait(.03));d.exchange.assert_not_called()
            release.set();first.join(1);second.join(1)
            self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
            self.assertEqual(errors,[]);self.assertEqual(d.can_bitrate,250000)
            d.exchange.assert_called_once()
        finally:
            release.set();first.join(2)
            if second.ident is not None:second.join(2)

    def test_provider_validates_full_list_then_commits(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,5,0,500000)
        for pairs,status in (([(1,250000),(99,0)],1), ([(3,1),(1,123)],10)):
            self.assertEqual(p.configure_timing(c,pairs=pairs)[0],status)
            p.session.configure_can_bitrate.assert_not_called()
        p.session.configure_can_bitrate.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(3,1)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[500000,0]))
        p.session.configure_can_bitrate.side_effect=None
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(3,1)])[0],0)
        p.session.configure_can_bitrate.assert_called_with(250000,loopback=True)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[250000,1]))

if __name__=='__main__':unittest.main()
