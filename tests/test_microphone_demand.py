import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from web.microphone_demand import MicrophoneDemand


class DemandTests(unittest.TestCase):
    def setUp(self):
        self.now=100.
        self.monitor=Mock()
        self.monitor.read.return_value={'ok':True,'activeSessions':0,'processIds':[]}
        self.server=SimpleNamespace(chain=SimpleNamespace(running=False),prefs={'mic':1,'ref':2,'out':3},runner_lock=threading.RLock())
        def start():
            self.server.chain.running=True
            return {'ok':True}
        def stop():
            self.server.chain.running=False
            return {'ok':True}
        self.server.start_audio=Mock(side_effect=start)
        self.server.stop_audio=Mock(side_effect=stop)
        self.server.list_devices=Mock(return_value={'capture':[{'name':'Test microphone','index':1}], 'loopback':[{'name':'Test reference','index':2}], 'playback':[{'name':'CABLE Input (VB-Audio Virtual Cable)','index':3}]})
        self.settings={'mode':'clean','onDemandMicrophone':True,'devices':{'mic':'Test microphone','ref':'Test reference','out':'CABLE Input (VB-Audio Virtual Cable)'}}
        self.profiles=Mock()
        self.demand=MicrophoneDemand(self.server,self.settings,self.profiles,lambda:False,self.monitor,lambda:self.now)
        with patch.object(self.demand,'_ensure_thread'):
            self.demand.start()

    def test_armed_idle_never_opens_the_physical_microphone(self):
        self.demand.tick()
        self.assertEqual(self.demand.state()['status'],'waiting')
        self.assertTrue(self.demand.armed)
        self.server.start_audio.assert_not_called()

    def test_consumer_starts_processing_and_closing_releases_it_after_grace(self):
        self.monitor.read.return_value={'ok':True,'activeSessions':1,'processIds':[123]}
        self.demand.tick()
        self.assertTrue(self.server.chain.running)
        self.assertEqual(self.demand.state()['consumerProcessIds'],[123])
        self.monitor.read.return_value={'ok':True,'activeSessions':0}
        self.now+=1
        self.demand.tick()
        self.server.stop_audio.assert_not_called()
        self.now+=3
        self.demand.tick()
        self.server.stop_audio.assert_called_once()
        self.assertFalse(self.server.chain.running)
        self.assertTrue(self.demand.armed)

    def test_short_session_reconnect_does_not_cut_off_processing(self):
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.demand.tick()
        self.monitor.read.return_value={'ok':True,'activeSessions':0}
        self.now+=2
        self.demand.tick()
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.now+=1
        self.demand.tick()
        self.server.stop_audio.assert_not_called()
        self.assertEqual(self.server.start_audio.call_count,1)

    def test_stop_disarms_and_does_not_restart_for_a_consumer(self):
        self.demand.stop()
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.demand.tick()
        self.assertFalse(self.demand.armed)
        self.server.start_audio.assert_not_called()
        self.monitor.close.assert_called_once()

    def test_monitor_failure_does_not_open_the_microphone_without_confirmed_use(self):
        self.monitor.read.return_value={'ok':False,'error':'cannot_detect'}
        self.demand.tick()
        self.server.start_audio.assert_not_called()
        self.now+=6
        self.demand.tick()
        self.server.start_audio.assert_not_called()
        self.assertEqual(self.demand.state()['status'],'monitor_unavailable')
        self.assertEqual(self.demand.state()['error'],'cannot_detect')

    def test_failed_start_does_not_retry_in_a_cpu_loop(self):
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.server.start_audio.side_effect=None
        self.server.start_audio.return_value={'ok':False,'error':'model_missing'}
        self.demand.tick()
        self.demand.tick()
        self.assertFalse(self.demand.armed)
        self.server.start_audio.assert_called_once()

    def test_continuous_mode_uses_the_original_start_path(self):
        self.demand.stop()
        self.settings['onDemandMicrophone']=False
        self.assertTrue(self.demand.start()['ok'])
        self.assertFalse(self.demand.armed)
        self.assertTrue(self.server.chain.running)

    def test_missing_enrollment_does_not_arm_target_capture(self):
        self.demand.stop()
        self.settings['mode']='target'
        self.profiles.current.return_value=None
        self.assertEqual(self.demand.start()['error'],'enrollment_required')
        self.assertFalse(self.demand.armed)

    def test_missing_named_device_never_uses_an_unrelated_input(self):
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.server.list_devices.return_value['capture']=[{'name':'Different microphone','index':1}]
        self.demand.tick()
        self.server.start_audio.assert_not_called()
        self.assertEqual(self.demand.error,'saved_devices_unavailable')
        self.assertFalse(self.demand.armed)

    def test_persistent_monitor_failure_releases_existing_capture(self):
        self.monitor.read.return_value={'ok':True,'activeSessions':1}
        self.demand.tick()
        self.monitor.read.return_value={'ok':False,'error':'monitor_lost'}
        self.demand.tick()
        self.assertTrue(self.server.chain.running)
        self.now+=6
        self.demand.tick()
        self.assertFalse(self.server.chain.running)
        self.assertEqual(self.demand.status,'monitor_unavailable')
        self.assertTrue(self.demand.armed)
