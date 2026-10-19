import unittest
from fishing_loop import FishingLoop

class Clock:
    now=100.0
    def __call__(self):return self.now

class Inputs:
    confirm_key=13
    def __init__(self):self.held=None;self.release_at=0;self.focus=True;self.cancel=False;self.presses=[];self.releases=[]
    def foreground(self):return self.focus
    def cancelled(self):return self.cancel
    def press(self,key,now):self.held=key;self.release_at=now+0.08;self.presses.append(key);return True
    def release(self):
        if self.held:self.releases.append(self.held);self.held=None
    def tick(self,now):
        if now>=self.release_at:self.release()

class Assist:
    def __init__(self):
        self.snapshot={'object':0x100000,'state':3,'pending':3,'frames':50,'result':0,'fish_id':1}
        self.menu={'selected':0,'rows':[{'index':0,'id':490,'quantity':2,'usable':True}]}
        self.queue=[]
    def status(self):return self.queue.pop(0) if self.queue else self.snapshot
    def bait_menu(self,*_):return self.menu
    def rod_choices(self,*_):return None

class FishingLoopTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.inputs=Inputs();self.assist=Assist()
        self.loop=FishingLoop(self.assist,self.inputs,lambda _:2,self.clock)
        self.loop.start();self.loop.tick()
    def advance(self,seconds=1):self.clock.now+=seconds;self.loop.tick()
    def state(self,state,result=0):
        self.assist.snapshot={**self.assist.snapshot,'state':state,'pending':state,'result':result}
        self.advance(0.1);self.advance()
    def test_confirm_selected_available_bait(self):
        self.advance();self.assertEqual(self.inputs.presses,[13]);self.assertEqual(self.loop.last_bait,490)
    def test_depleted_bait_switches_to_an_available_row(self):
        self.assist.menu={'selected':0,'rows':[{'index':0,'id':490,'quantity':0,'usable':False},{'index':2,'id':491,'quantity':9,'usable':True}]}
        self.advance();self.assertEqual(self.inputs.presses,[0x28])
        self.assist.menu['selected']=2;self.advance()
        self.assertEqual(self.inputs.presses,[0x28,13]);self.assertEqual(self.loop.last_bait,491)
    def test_empty_or_unusable_baits_stop_without_confirming(self):
        self.assist.menu['rows'][0]['usable']=False
        self.advance(2);self.assertFalse(self.loop.enabled);self.assertEqual(self.inputs.presses,[])
    def test_waits_for_native_catch_then_confirms_results_and_repeats(self):
        self.state(6);self.assertEqual(self.inputs.presses,[])
        self.state(7);self.assertEqual(self.inputs.presses,[])
        self.state(17);self.assertEqual(self.loop.rounds,1);self.assertEqual(self.inputs.presses,[13])
        self.advance();self.assertEqual(self.loop.rounds,1)
        self.state(3);self.assertEqual(self.loop.last_bait,490)
        self.state(6);self.state(17);self.assertEqual(self.loop.rounds,2)
    def test_cancel_releases_owned_key(self):
        self.advance();self.inputs.cancel=True;self.advance(0.01)
        self.assertFalse(self.loop.enabled);self.assertIsNone(self.inputs.held)
    def test_focus_loss_releases_and_pauses_deadline(self):
        self.advance();n=len(self.inputs.presses);self.inputs.focus=False;self.advance(0.01)
        self.assertIsNone(self.inputs.held);self.advance(500)
        self.assertTrue(self.loop.enabled);self.assertEqual(len(self.inputs.presses),n)
        self.inputs.focus=True;self.advance(0.1);self.assertTrue(self.loop.enabled)
        self.advance();self.assertGreater(len(self.inputs.presses),n)
    def test_leaving_or_replacing_object_stops_input(self):
        self.assist.snapshot={**self.assist.snapshot,'object':0x200000}
        self.advance();self.assertFalse(self.loop.enabled);self.assertEqual(self.inputs.presses,[])
    def test_transition_is_rechecked_before_press(self):
        original=dict(self.assist.snapshot)
        changed={**original,'state':26,'pending':26}
        self.assist.queue=[original,changed];self.advance()
        self.assertEqual(self.inputs.presses,[])
    def test_pending_state_does_not_receive_input(self):
        self.assist.snapshot['pending']=4;self.advance();self.assertEqual(self.inputs.presses,[])
    def test_menu_navigation_retries_then_stops(self):
        self.assist.menu={'selected':0,'rows':[{'index':2,'id':491,'quantity':9,'usable':True}]}
        for _ in range(9):self.advance()
        self.assertFalse(self.loop.enabled);self.assertIn(0x53,self.inputs.presses)
    def test_stalled_state_stops_and_releases(self):
        self.advance();self.advance(91)
        self.assertFalse(self.loop.enabled);self.assertIsNone(self.inputs.held)
    def test_failed_bait_is_not_repeated(self):
        self.advance();self.state(6);self.state(24,result=1)
        self.assist.menu['rows'].append({'index':1,'id':491,'quantity':2,'usable':True})
        self.state(3);self.assertIn(490,self.loop.failed_baits);self.assertEqual(self.inputs.presses[-1],0x28)
    def test_not_in_fishing_refuses_start(self):
        self.loop.stop();self.assist.snapshot=None
        with self.assertRaises(RuntimeError):self.loop.start()
        self.assertFalse(self.loop.enabled)
    def test_no_bite_dialog_confirms_switches_bait_and_resumes(self):
        self.advance();self.state(6)
        self.assist.snapshot['no_bite']=True
        self.state(29,result=0)  # Result flag can still describe an earlier catch.
        self.assertTrue(self.loop.enabled);self.assertIn(490,self.loop.failed_baits)
        self.assertEqual(self.inputs.presses[-1],13);self.assertEqual(self.loop.rounds,1)
        self.assertIn('换饵',self.loop.message)
        self.assist.snapshot['no_bite']=False;self.state(24)
        self.assist.menu['rows'].append({'index':1,'id':491,'quantity':2,'usable':True})
        self.state(3);self.assertTrue(self.loop.enabled);self.assertEqual(self.inputs.presses[-1],0x28)
        self.assist.menu['selected']=1;self.advance();self.assertEqual(self.loop.last_bait,491)
        self.state(6);self.state(17);self.assertEqual(self.loop.rounds,2)
    def test_no_bite_dialog_cancellation_releases_without_confirming(self):
        self.advance();self.state(6);count=len(self.inputs.presses)
        self.assist.snapshot.update(state=29,pending=29,no_bite=True)
        self.inputs.cancel=True;self.advance()
        self.assertFalse(self.loop.enabled);self.assertIsNone(self.inputs.held)
        self.assertEqual(len(self.inputs.presses),count)
    def test_no_bite_during_selection_switches_without_counting_twice(self):
        self.advance();self.assist.snapshot['no_bite']=True;self.state(29)
        self.assertEqual(self.loop.rounds,1);self.assertIn(490,self.loop.failed_baits)
        self.assist.snapshot['no_bite']=False
        self.assist.menu['rows'].append({'index':1,'id':491,'quantity':2,'usable':True})
        self.state(3);self.assertEqual(self.inputs.presses[-1],0x28)
        self.assertFalse(self.loop.result_seen)
        self.assist.menu['selected']=1;self.advance();self.state(6);self.state(17)
        self.assertEqual(self.loop.rounds,2)
    def test_changes_owned_rod_when_no_current_rod_bait_is_available(self):
        self.assist.menu={'selected':0,'rows':[{'index':0,'id':491,'quantity':9,'usable':False,'suitable':True}]}
        self.assist.rod_choices=lambda *_:{'selected':0,'rods':[{'index':0,'id':480,'baits':{490}},{'index':1,'id':482,'baits':{491}}]}
        self.advance(2);self.assertEqual(self.inputs.presses,[0x27])
        self.assist.menu['rows'][0]['usable']=True
        self.advance();self.assertEqual(self.inputs.presses,[0x27,13])
