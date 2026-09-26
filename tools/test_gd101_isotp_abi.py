import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from gd101_isotp import ISOTPMessage
from test_gd101_abi_deadline import Exports

class ISOABITests(unittest.TestCase):
    def provider(self,flags=0):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        self.assertEqual(p.connect(1,6,flags,500000)[0],0)
        return p

    def test_lifecycle_filter_commit_and_failure(self):
        p=self.provider();channel=p.channel
        p.session.configure_isotp_routes.assert_called_once_with({})
        self.assertEqual(p.session.start_receiver.call_args.kwargs['isotp'].routes,{})
        args=(channel,6,0,b'\xff'*4,bytes.fromhex('000007e8'),bytes.fromhex('000007e0'))
        status,handle=p.start_flow_filter(*args);self.assertEqual(status,0)
        self.assertEqual(p.start_flow_filter(*args)[0],24)
        self.assertEqual(p.start_flow_filter(channel,6,0x40,args[3],args[4],args[5])[0],24)
        self.assertEqual(p.start_flow_filter(channel,6,0,args[3],args[4],bytes.fromhex('000007e1'))[0],24)
        entry=p.isotp_filters[handle]
        self.assertEqual(entry['slot'],0)
        self.assertEqual(p.write_message(channel,6,0,args[-1]+b'\x22\x01',100),0)
        p.session.write_isotp_ordered.assert_called_once_with(0,b'\x22\x01',timeout=.1)
        before=dict(p.isotp_filters);p.session.configure_isotp_routes.side_effect=RuntimeError('Rejected table')
        with self.assertRaises(RuntimeError):p.stop_filter(channel,handle)
        self.assertEqual(p.isotp_filters,before)
        p.session.configure_isotp_routes.side_effect=None
        self.assertEqual(p.stop_filter(channel,handle),0)
        self.assertEqual(p.stop_filter(channel,handle),22)
        self.assertEqual(p.write_message(channel,6,0,args[-1]+b'\x22',100),23)
        self.assertEqual(p.disconnect(channel),0);p.session.disconnect_can.assert_called_once()

    def test_linux_extended_address_filter_read_write(self):
        p=self.provider(0x100);ffi=Exports();install(ffi,p);channel=p.channel
        msgs=[ffi.new('GD101_MESSAGE *') for _ in range(3)]
        data=[b'\xff'*5,bytes.fromhex('18daf110aa'),bytes.fromhex('18da10f1bb')]
        for m,d in zip(msgs,data):
            m.ProtocolID=6;m.TxFlags=0x180;m.DataSize=5;ffi.memmove(m.Data,d,5)
        handle=ffi.new('uint32_t *')
        self.assertEqual(ffi.functions['PassThruStartMsgFilter'](channel,3,*msgs,handle),0)
        p.session.read_message.return_value=ISOTPMessage(0x18daf110,b'\x62\x01',True,0xaa,123,124,0)
        out=ffi.new('GD101_MESSAGE *');count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](channel,out,count,0),0)
        self.assertEqual((out.ProtocolID,out.RxStatus,out.Timestamp,out.DataSize),(6,0x180,124,7))
        self.assertEqual(bytes(ffi.buffer(out.Data,7)),data[1]+b'\x62\x01')
        p.session.read_message.side_effect=TimeoutError()
        count[0]=1;self.assertEqual(ffi.functions['PassThruReadMsgs'](channel,out,count,0),16)
        self.assertEqual(count[0],0)

    def test_invalid_flows_and_failed_connect_publish_nothing(self):
        p=self.provider();c=p.channel
        self.assertEqual(p.start_flow_filter(c,6,0,bytes(4),bytes(4),bytes(4))[0],1)
        self.assertEqual(p.start_flow_filter(c,6,0x100,b'\xff'*4,bytes(4),bytes(4))[0],6)
        self.assertEqual(p.isotp_filters,{})
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.configure_isotp_routes.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):p.connect(1,6,0,500000)
        self.assertIsNone(p.channel);p.session.start_receiver.assert_not_called()
        p.session.disconnect_can.assert_called_once()


class ISOFailureTests(unittest.TestCase):
    provider=ISOABITests.provider
    def test_route_timeout_preserves_handles_across_rpc(self):
        import struct
        from gd101_bridge import dispatch,REQUEST,RESPONSE,MAGIC
        p=self.provider();c=p.channel
        mask=b'\xff'*4;rx=bytes.fromhex('000007e8');tx=bytes.fromhex('000007e0')
        _,handle=p.start_flow_filter(c,6,0,mask,rx,tx)
        before=dict(p.isotp_filters);next_id=p.next_id
        p.session.configure_isotp_routes.side_effect=TimeoutError('Missing acknowledgement')
        requests=[(21,c,6,0,0,mask+bytes.fromhex('000007e9')+bytes.fromhex('000007e1')),
                  (8,c,handle,0,0,b''),(9,c,0,0,0,b'')]
        for op,a,b,d,e,payload in requests:
            response=dispatch(p,REQUEST.pack(MAGIC,1,123,1,op,a,b,d,e,len(payload))+payload)
            self.assertEqual(RESPONSE.unpack_from(response)[2],9)
            self.assertEqual(p.isotp_filters,before)
            self.assertEqual(p.next_id,next_id)
        self.assertIn('uncertain',p.last_error)

    def test_malformed_receive_preserves_linux_output(self):
        p=self.provider();ffi=Exports();install(ffi,p)
        out=ffi.new('GD101_MESSAGE *');out.ProtocolID=99
        count=ffi.new('uint32_t *')
        for payload in (b'',b'x'*4096):
            p.session.read_message.return_value=ISOTPMessage(0x7e8,payload,False,None,1,1,0)
            count[0]=1
            self.assertEqual(ffi.functions['PassThruReadMsgs'](p.channel,out,count,0),7)
            self.assertEqual(count[0],0)
            self.assertEqual(out.ProtocolID,99)

if __name__=='__main__':unittest.main()
