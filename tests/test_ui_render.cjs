// Exercise the running UI with real-shaped stage data, without audio devices.
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=readFileSync(join(__dirname,'../web/ui/studio.js'),'utf8');
const paint=source.slice(source.indexOf('function paint(){'),source.indexOf('const histories='));
function element(){return {value:'45',textContent:'',hidden:false,style:{},options:[],children:[],classList:{toggle(){}},lastElementChild:{},replaceChildren(...nodes){this.children=nodes},append(...nodes){this.children.push(...nodes)},getAttribute(k){return this[k]},setAttribute(k,v){this[k]=v},pause(){},querySelector(){return {disabled:false}}};}
for(const extractionModel of ['speakerbeam','ps4','verified'])for(const mode of ['clean','target'])for(const running of [false,true])for(const diagnosticState of ['idle','capturing','complete']){
 const elements=new Map();
 const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const snapshot={audio:{running,inRms:.01,outRms:.005,frames:50,stageLevels:{aec:.009,target:.006,denoise:.005,output:.005},target:{enabled:mode==='target',bufferMs:2000,inferenceMs:110}},settings:{mode,processor:'auto',cleanSampleRate:48000,extractionModel},enrollment:{status:'idle',active:false},hardware:{cudaAvailable:true,gpuName:'RTX',cudaVersion:'13.0',toolkitVersion:'13.4.3'},profile:null};
 snapshot.profile={name:'test',duration:45,speechSeconds:30,consistency:.75}; snapshot.diagnostic={status:diagnosticState,active:diagnosticState==='capturing',kind:'duet',phase:'other',elapsed:15,duration:36,report:diagnosticState==='complete'?{audio:{raw:'/raw',processed:'/processed'},duetSummary:{yourSoloWindows:5,otherSoloWindows:5,yourMedianRatio:.9,otherMedianAttenuationDb:30,yourShortDrops:{weakFrames:1,voicedFrames:40}}}:null};
 Object.assign(snapshot.settings,{startWithWindows:true,startInBackground:true,autoStartProcessing:true,closeToTray:true}); snapshot.startup={supported:true,backgroundAvailable:true}; snapshot.gpuSetup={status:'ready',active:false};
 const context={snapshot,$,document:{querySelectorAll(){return []},createElement:element},busy:false,lastRecord:'idle',pair:zh=>zh,t:x=>x,describe:x=>x,toast(){},console};
 vm.runInNewContext(paint+'\npaint();',context);
 assert.equal($('startWithWindows').checked,true);assert.equal($('startInBackground').checked,true);assert.equal($('gpuInstall').disabled,true);assert.equal($('goBackground').disabled,diagnosticState==='capturing');
 if(diagnosticState==='capturing'){assert.ok($('diagnosticStatus').textContent.includes('15.0/36'));assert.equal($('duetDiagnostic').disabled,true);}
 if(diagnosticState==='complete'){assert.equal($('diagnosticRawAudio').src,'/raw');assert.equal($('diagnosticAudioPanel').hidden,running);assert.ok($('duetSummary').textContent.includes('1/40'));}
 if(running){
  assert.equal($('inDb').textContent,'-40.0 dB');
  assert.ok($('stageLevels').textContent.includes('OUT')||$('stageLevels').textContent.includes('输出'));
  assert.equal($('frameCount').textContent,'50 frames');if(mode==='target')assert.ok($('chainLabel').textContent.includes({speakerbeam:'SpeakerBeam',ps4:'PS4',verified:'本人语音保护'}[extractionModel]));
 }
}
console.log('Passed: stopped/running UI rendering, clean/target modes, stage levels and microphone meter.');
const diagnosticFunctions=source.slice(source.indexOf('function diagnosticConfigKey'),source.indexOf('function paintGpuSetup'));
function diagnosticView(diag,settings){
 const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const context={diag,$,document:{createElement:element},pair:zh=>zh,describe:value=>value};
 if(settings)context.snapshot={settings};
 vm.runInNewContext(diagnosticFunctions+'\npaintDiagnostic(diag,false,false,true);',context);
 return $;
}
for(const [elapsed,expected,remaining]of [[0,'只有你说话','12'],[11.9,'只有你说话','1'],[12,'只有对方说话','12'],[24,'两人一起说话','12'],[35.9,'两人一起说话','1']]){
 const $=diagnosticView({status:'capturing',kind:'duet',elapsed,level:.01,outputLevel:.001});
 assert.ok($('diagAction').textContent.includes(expected));assert.equal($('diagCountdown').textContent,remaining);
 assert.equal($('diagLiveMeters').hidden,false);assert.equal($('diagResults').hidden,true);
 assert.equal($('diagRawMeter').style.width,'33.33333333333333%');
}
for(const status of ['preparing','countdown','analyzing','error']){
 const $=diagnosticView({status,kind:'duet',countdown:3,error:'test_error'});
 assert.equal($('diagLiveMeters').hidden,true);assert.equal($('diagResults').hidden,true);
}
const report={windows:[{second:2,rawRms:.04,finalRms:.02},{second:14,rawRms:.04,finalRms:.0004},{second:26,rawRms:.04,finalRms:.03}],shortDrops:{weakFrames:0,voicedFrames:40}};
{
 const $=diagnosticView({status:'complete',kind:'duet',elapsed:36,report});
 assert.equal($('diagOwnMetric').textContent,'50%');assert.equal($('diagOtherMetric').textContent,'40.0 dB');assert.equal($('diagDropsMetric').textContent,'0 / 40');
 assert.equal($('diagChart').children.length,18);assert.ok($('diagChart').children[0].className.includes('missing'));
 assert.equal($('diagChart').children[1].children.length,2);assert.ok($('diagChart').children[1].title.includes('原声'));
 assert.ok(Math.abs(parseFloat($('diagChart').children[1].children[1].style.height)-43.3676665)<.0001);
}
{
 const $=diagnosticView({status:'complete',kind:'single',elapsed:12,report:{windows:[{second:2,rawRms:.01,aecRms:.02,finalRatio:.5}]}});
 assert.equal($('diagOwnMetric').textContent,'100%');assert.equal($('diagOtherMetric').textContent,'—');
 assert.equal($('diagPhase1').hidden,true);assert.equal($('diagPhase2').hidden,true);assert.equal($('diagChart').children.length,6);
}
{
 const $=diagnosticView({status:'complete',kind:'duet',report:{insufficientInput:true,windows:[{second:2,rawRms:.01,finalRms:0}]}});
 assert.equal($('diagOwnMetric').textContent,'0%');assert.equal($('diagOtherMetric').textContent,'—');assert.equal($('diagDropsMetric').textContent,'—');
 assert.ok($('diagResultNote').textContent.includes('缺少有效声音'));assert.equal($('diagChart').children[1].children[1].style.height,'0%');
}
console.log('Passed: phase guidance, countdown, measured charts, missing data, silence and single-speaker results.');
{
 const controls=new Map(),$=id=>{if(!controls.has(id))controls.set(id,{paused:0,played:0,pause(){this.paused++},play(){this.played++;return Promise.resolve()}});return controls.get(id)};
 const snapshot={audio:{running:false},diagnostic:{active:false},enrollment:{active:false}};
 const handlers=source.slice(source.indexOf('for(const [button,player,other]'),source.indexOf("$('record').onclick"));
 vm.runInNewContext(handlers,{$,snapshot,toast(){}});
 $('playDiagnosticOutput').onclick();
 assert.equal($('diagnosticProcessedAudio').played,1);assert.equal($('diagnosticRawAudio').paused,1);
 snapshot.audio.running=true;$('playDiagnosticRaw').onclick();assert.equal($('diagnosticRawAudio').played,0);
}
console.log('Passed: processed-voice playback button and active-processing guard.');
{
 const parameters={extractionModel:'verified',processor:'auto',audioPreferences:{profile:1,nsBackend:'webrtc',micGain:1},targetControls:{suppressionDb:40,identityThreshold:.35,shortWordProtection:true}};
 const fresh=diagnosticView({status:'complete',kind:'duet',report:{...report,parameters}},parameters);
 assert.ok(fresh('diagParametersStatus').textContent.includes('同一组参数'));
 const changed={...parameters,audioPreferences:{...parameters.audioPreferences,micGain:1.3}};
 const stale=diagnosticView({status:'complete',kind:'duet',report:{...report,parameters}},changed);
 assert.ok(stale('diagParametersStatus').textContent.includes('参数已修改'));
}
{
 const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 Object.entries({diagModel:'verified',diagProcessor:'auto',diagAec:'3',diagNoise:'off',diagGain:'130',diagSuppression:'24',diagIdentity:'40'}).forEach(([id,value])=>$(id).value=value);
 $('diagShortProtection').checked=true;
 let sent;
 const context={$,api:(path,body)=>{sent={path,body};return Promise.resolve({ok:true})}};
 const save=source.slice(source.indexOf('async function saveDiagnosticSettings'),source.indexOf("for(const id of ['diagModel'"));
 vm.runInNewContext(save+'\nsaveDiagnosticSettings();',context);
 assert.equal(sent.path,'/api/studio/settings');assert.equal(sent.body.audioPreferences.micGain,1.3);assert.equal(sent.body.audioPreferences.profile,3);
 assert.equal(sent.body.targetControls.suppressionDb,24);assert.equal(sent.body.targetControls.identityThreshold,.4);
}
console.log('Passed: saved tuning values and stale-preview warnings.');
{
 const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const snapshot={audio:{running:false},settings:{onDemandMicrophone:true},microphoneDemand:{armed:true,status:'waiting'}};
 const helpers=source.slice(source.indexOf('function paintMicrophoneDemand'),source.indexOf('function paintDiagnosticSettings'));
 vm.runInNewContext(helpers+'\npaintMicrophoneDemand(snapshot.audio,snapshot.settings,false);',{$,snapshot,busy:false,pair:zh=>zh,t:value=>value,describe:value=>value});
 assert.ok($('transportTitle').textContent.includes('按需待命'));assert.equal($('onDemandMicrophone').disabled,true);
 assert.ok($('microphoneDemandStatus').textContent.includes('物理麦克风已释放'));
 snapshot.audio.running=true;snapshot.microphoneDemand.status='processing';snapshot.microphoneDemand.clients=['voice-app'];
 vm.runInNewContext(helpers+'\npaintMicrophoneDemand(snapshot.audio,snapshot.settings,false);',{$,snapshot,busy:false,pair:zh=>zh,t:value=>value,describe:value=>value});
 assert.ok($('microphoneDemandStatus').textContent.includes('voice-app'));
}
console.log('Passed: on-demand standby and consuming-application status.');

const missingCableExpression = source.match(/const missing=([^;]+);\$\('deviceError'\)/)[1];
for (const [playback, expected] of [
 [[{name:'扬声器 (VB-Audio Virtual Cable)',isCableInput:true}],false],
 [[{name:'CABLE In 16 Ch (VB-Audio Virtual Cable)',isCableInput:false}],true],
 [[{name:'CABLE Input (VB-Audio Virtual Cable)'}],false],
 [[{name:'Speakers (Realtek Audio)',isCableInput:false}],true]
]) {
 assert.equal(vm.runInNewContext(missingCableExpression,{inventory:{playback}}),expected);
}
console.log('Passed: localized virtual output and exclusion of the 16-channel port.');
