from pathlib import Path
import tempfile
import threading
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web import startup
from web import gui


class StartupTests(unittest.TestCase):
    def test_real_runner_lock_supports_nested_startup_paths(self):
        from web import server
        with server.runner_lock:
            acquired = server.runner_lock.acquire(blocking=False)
            self.assertTrue(acquired)
            if acquired:
                server.runner_lock.release()

    def server(self):
        return SimpleNamespace(chain=SimpleNamespace(running=False),prefs={},runner_lock=threading.RLock(),
            start=Mock(return_value={'ok':True}),list_devices=Mock(return_value={
                'capture':[{'name':'USB Microphone','index':1}],
                'loopback':[{'name':'Headphones [Loopback]','index':2}],
                'playback':[{'name':'CABLE Input (VB-Audio Virtual Cable)','index':3}]}))

    def settings(self):
        return {'mode':'target','devices':{'mic':'USB Microphone','ref':'Headphones [Loopback]',
                'out':'CABLE Input (VB-Audio Virtual Cable)'},
                'audioPreferences':{'profile':1,'nsBackend':'webrtc','micGain':1.5}}

    def test_saved_processing_restores_only_the_named_devices_and_audio_preferences(self):
        server=self.server()
        result=startup.start_saved_processing(server,self.settings(),True)
        self.assertTrue(result['ok'])
        server.start.assert_called_once()
        self.assertEqual(server.prefs,{'mic':1,'ref':2,'out':3,'profile':1,
                                      'nsBackend':'webrtc','micGain':1.5,'listenToSelf':False})

    def test_missing_voiceprint_never_opens_the_microphone(self):
        server=self.server()
        result=startup.start_saved_processing(server,self.settings(),False)
        self.assertEqual(result['error'],'enrollment_required')
        server.list_devices.assert_not_called()
        server.start.assert_not_called()

    def test_missing_saved_device_never_substitutes_a_default_microphone(self):
        server=self.server()
        server.list_devices.return_value['capture']=[{'name':'Another microphone','index':9}]
        result=startup.start_saved_processing(server,self.settings(),True)
        self.assertEqual(result['error'],'saved_devices_unavailable')
        server.start.assert_not_called()

    def test_automatic_processing_never_routes_to_physical_speakers(self):
        server=self.server()
        settings=self.settings()
        settings['devices']['out']='Speakers'
        result=startup.start_saved_processing(server,settings,True)
        self.assertEqual(result['error'],'saved_virtual_output_unavailable')
        server.start.assert_not_called()

    def test_startup_registration_quotes_the_installed_launcher_and_is_per_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'Application With Spaces'
            root.mkdir()
            (root/'AECStudio.exe').touch()
            registry=Mock()
            registry.HKEY_CURRENT_USER=1
            registry.KEY_SET_VALUE=2
            registry.KEY_QUERY_VALUE=4
            registry.REG_SZ=1
            registry.CreateKeyEx.return_value.__enter__=Mock(return_value='key')
            registry.CreateKeyEx.return_value.__exit__=Mock(return_value=False)
            with patch.object(startup,'winreg',registry),patch.object(startup.sys,'platform','win32'):
                startup.register_startup(True,root,True,True)
            args=registry.SetValueEx.call_args.args
            self.assertEqual(args[-1],'"'+str((root/'AECStudio.exe').resolve())+'" --background --auto-start')
            self.assertEqual(registry.CreateKeyEx.call_args.args[0],1)

    def test_disabling_startup_does_not_delete_another_installation_entry(self):
        registry=Mock()
        registry.KEY_SET_VALUE=2
        registry.KEY_QUERY_VALUE=4
        registry.CreateKeyEx.return_value.__enter__=Mock(return_value='key')
        registry.CreateKeyEx.return_value.__exit__=Mock(return_value=False)
        registry.QueryValueEx.return_value=('"C:\\Another Installation\\AECStudio.exe"',1)
        with tempfile.TemporaryDirectory() as directory,patch.object(startup,'winreg',registry),patch.object(startup.sys,'platform','win32'):
            startup.register_startup(False,directory)
        registry.DeleteValue.assert_not_called()


class BackgroundTests(unittest.TestCase):
    def test_tray_stop_disarms_a_waiting_microphone(self):
        def immediate_thread(target,**kwargs):
            return SimpleNamespace(start=target)
        response=io.BytesIO(json.dumps({'running':False,'onDemandArmed':True}).encode())
        with patch.object(gui.urllib.request,'urlopen',return_value=response),patch.object(gui,'_post',return_value={'ok':True}) as post,patch.object(gui.threading,'Thread',side_effect=immediate_thread):
            gui._tray_toggle_processing()
        post.assert_called_once_with('/api/stop')

    def test_background_launch_does_not_create_a_webview_before_open_is_requested(self):
        view=Mock()
        with patch.dict('sys.modules',{'webview':view}),patch.object(gui,'_port_answers',return_value=True), \
             patch.object(gui,'_startup_options',return_value={}),patch.object(gui,'_start_tray',return_value=True), \
             patch.object(gui,'_wait_for_open',return_value=False),patch.object(gui,'_console_minimize'), \
             patch.object(gui,'_shutdown'),patch.object(gui.sys,'platform','test'):
            self.assertEqual(gui.main(['--background','--no-auto-start']),0)
        view.create_window.assert_not_called()

    def test_auto_processing_retries_missing_devices_without_switching_modes(self):
        quit_event=Mock()
        quit_event.is_set.return_value=False
        quit_event.wait.return_value=False
        with patch.object(gui,'_quit_event',quit_event),patch.object(gui,'_post',side_effect=[
                {'ok':False,'error':'saved_devices_unavailable'},{'ok':True}]) as post:
            gui._auto_start_processing()
        self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args.args,('/api/app/start-saved',))

    def test_auto_processing_does_not_keep_retrying_a_missing_voiceprint(self):
        quit_event=Mock()
        quit_event.is_set.return_value=False
        with patch.object(gui,'_quit_event',quit_event),patch.object(gui,'_post',return_value={
                'ok':False,'error':'enrollment_required'}) as post:
            gui._auto_start_processing()
        post.assert_called_once()

    def test_closing_to_tray_keeps_the_processing_session(self):
        with patch.object(gui,'_quitting',False),patch.object(gui,'_prefs',return_value={'trayEnabled':True}), \
             patch.object(gui,'_background_window',return_value=True) as hide:
            self.assertFalse(gui._on_closing())
        hide.assert_called_once()


if __name__=='__main__':
    unittest.main()
