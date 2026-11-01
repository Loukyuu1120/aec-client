import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from web import server
from web.startup import STARTUP_FLAGS


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch('web.studio_routes.DATA', Path(self.temp.name))
        self.patch.start()
        self.client = TestClient(server.app)
        self.original_model = self.client.get('/api/studio/state').json()['settings']['extractionModel']
        self.original_startup = {key:self.client.get('/api/studio/state').json()['settings'][key] for key in STARTUP_FLAGS}
        self.original_audio=self.client.get('/api/studio/state').json()['settings']['audioPreferences']
        self.original_controls=self.client.get('/api/studio/state').json()['settings']['targetControls']

    def tearDown(self):
        with patch('web.studio_routes.register_startup'):
            self.client.post('/api/studio/settings', json={'language':'zh-CN','mode':'clean','processor':'auto',
                                                         'extractionModel':self.original_model,'audioPreferences':self.original_audio,
                                                         'targetControls':self.original_controls,**self.original_startup})
        self.patch.stop()
        self.temp.cleanup()

    def test_language_persists_without_opening_microphone(self):
        r = self.client.post('/api/studio/settings', json={'language':'en'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get('/api/studio/state').json()['settings']['language'], 'en')
        self.assertFalse(server.chain.running)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_invalid_language_does_not_change_settings(self):
        before = self.client.get('/api/studio/state').json()['settings']['language']
        self.assertEqual(self.client.post('/api/studio/settings', json={'language':'bad'}).status_code, 422)
        self.assertEqual(self.client.get('/api/studio/state').json()['settings']['language'], before)

    def test_learning_can_be_paused_live_and_reset_requires_stopped_audio(self):
        original = self.client.get('/api/studio/state').json()['settings']['adaptiveLearning']
        try:
            with patch.object(server.chain, 'running', True):
                self.assertEqual(self.client.post('/api/studio/settings', json={'adaptiveLearning':True}).status_code, 200)
                self.assertEqual(self.client.post('/api/studio/settings', json={'adaptiveLearning':False}).status_code, 200)
                with patch('web.studio_routes.reset_adaptation') as reset:
                    self.assertEqual(self.client.post('/api/voiceprint/reset-learning', json={}).status_code, 409)
                    reset.assert_not_called()
            with patch('web.studio_routes.reset_adaptation') as reset:
                self.assertEqual(self.client.post('/api/voiceprint/reset-learning', json={}).status_code, 200)
                reset.assert_called_once()
            self.assertEqual(self.client.post('/api/studio/settings', json={'adaptiveLearning':'yes'}).status_code, 422)
        finally:
            self.client.post('/api/studio/settings', json={'adaptiveLearning':original})

    def test_running_session_blocks_processor_change(self):
        with patch.object(server.chain, 'running', True):
            self.assertEqual(self.client.post('/api/studio/settings', json={'processor':'cpu'}).status_code, 409)

    def test_cross_origin_microphone_request_is_blocked(self):
        r = self.client.post('/api/voiceprint/record', json={}, headers={'Origin':'https://example.com'})
        self.assertEqual(r.status_code, 403)

    def test_tuning_persists_and_invalid_values_leave_previous_settings_intact(self):
        controls={'suppressionDb':24,'identityThreshold':.4,'shortWordProtection':True}
        audio={'profile':3,'nsBackend':'off','micGain':1.25}
        response=self.client.post('/api/studio/settings',json={'targetControls':controls,'audioPreferences':audio})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['targetControls'],controls)
        self.assertEqual(server.prefs['micGain'],1.25)
        self.assertEqual(server.prefs['profile'],3)
        self.assertEqual(self.client.post('/api/studio/settings',json={'targetControls':{'suppressionDb':100}}).status_code,422)
        self.assertEqual(self.client.get('/api/studio/state').json()['settings']['targetControls'],controls)
        with patch.object(server.chain,'running',True):
            self.assertEqual(self.client.post('/api/studio/settings',json={'targetControls':controls}).status_code,409)
            self.assertEqual(self.client.post('/api/studio/settings',json={'audioPreferences':audio}).status_code,409)

    def test_invalid_tuning_does_not_reconfigure_loaded_models(self):
        with patch.object(server.app.state.target_model,'configure') as encoder,patch.object(server.app.state.beam_model,'configure') as beam:
            response=self.client.post('/api/studio/settings',json={'processor':'auto','targetControls':{'suppressionDb':100}})
        self.assertEqual(response.status_code,422)
        encoder.assert_not_called()
        beam.assert_not_called()

    def test_target_mode_without_enrollment_does_not_open_output(self):
        self.client.post('/api/studio/settings', json={'mode':'target'})
        with patch.object(server.app.state.voice_profiles, 'current', return_value=None), patch('web.server.AudioRunner') as runner:
            r = self.client.post('/api/start', json={})
            self.assertFalse(r.json()['ok'])
            runner.assert_not_called()

    def test_diagnostic_respects_selected_extraction_model(self):
        diagnostic = server.app.state.voice_diagnostic
        for selected in ('ps4', 'speakerbeam', 'verified'):
            with self.subTest(model=selected):
                response = self.client.post('/api/studio/settings', json={'extractionModel':selected})
                self.assertEqual(response.status_code, 200)
                with patch('web.server.list_devices', return_value={'capture':[{'index':1}],
                          'loopback':[{'index':2}]}), patch.object(diagnostic, 'start') as start:
                    response = self.client.post('/api/diagnostics/voice', json={'mic':1,'ref':2,'kind':'duet'})
                self.assertEqual(response.status_code, 200)
                settings=self.client.get('/api/studio/state').json()['settings']
                start.assert_called_once_with(1, 2, kind='duet', save_audio=False, extraction_model=selected,
                                              audio_preferences=settings['audioPreferences'],target_controls=settings['targetControls'])

    def test_invalid_startup_setting_never_registers_a_windows_startup_entry(self):
        with patch('web.studio_routes.register_startup') as register:
            response=self.client.post('/api/studio/settings',json={'startWithWindows':'yes'})
        self.assertEqual(response.status_code,422)
        register.assert_not_called()

    def test_background_startup_preferences_are_saved_explicitly(self):
        values={key:True for key in STARTUP_FLAGS}
        with patch('web.studio_routes.register_startup') as register:
            response=self.client.post('/api/studio/settings',json=values)
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(response.json()[key] for key in STARTUP_FLAGS))
        self.assertTrue(register.call_args.args[0])
        self.assertEqual(register.call_args.args[2:],(True,True))
        self.assertTrue(self.client.get('/api/prefs').json()['trayEnabled'])

    def test_startup_registration_failure_preserves_the_old_preferences(self):
        before=self.client.get('/api/studio/state').json()['settings']['startWithWindows']
        with patch('web.studio_routes.register_startup',side_effect=OSError('policy blocked')):
            response=self.client.post('/api/studio/settings',json={'startWithWindows':not before})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.get('/api/studio/state').json()['settings']['startWithWindows'],before)

    def test_gpu_setup_cannot_interrupt_a_running_audio_session(self):
        with patch.object(server.chain,'running',True),patch.object(server.app.state.gpu_manager,'install_async') as install:
            response=self.client.post('/api/gpu/install',json={})
        self.assertEqual(response.status_code,409)
        install.assert_not_called()

    def test_saved_start_without_enrollment_does_not_start_processing(self):
        self.client.post('/api/studio/settings',json={'mode':'target'})
        with patch.object(server.app.state.voice_profiles,'current',return_value=None),patch('web.server.start') as start:
            response=self.client.post('/api/app/start-saved',json={})
        self.assertFalse(response.json()['ok'])
        self.assertEqual(response.json()['error'],'enrollment_required')
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
