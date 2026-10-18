"""Fishing-only input decisions with injected input and clock for testing."""
import time

CONFIRM_STATES=frozenset((4,5,17,18,19,20,21,22,23,24,25,29))

class FishingLoop:
    def __init__(self,assist,inputs,quantity,clock=time.monotonic):
        self.assist=assist;self.inputs=inputs;self.quantity=quantity;self.clock=clock
        self.enabled=False;self.message='全自动钓鱼未开启';self.rounds=0
        self.object=None;self.state=None;self.last_action=0;self.state_since=0
        self.paused_at=None;self.had_cast=False;self.result_seen=False
        self.last_bait=None;self.failed_baits=set();self.nav_marker=None;self.nav_attempts=0

    def start(self):
        status=self.assist.status()
        if not status or status['state'] in (-1,0,1,2,26,27,28):
            raise RuntimeError('请先在钓鱼点进入鱼竿／鱼饵选择界面，再开启全自动钓鱼')
        self.object=status['object'];self.enabled=True;self.rounds=0
        self.state=None;self.last_action=self.clock();self.paused_at=None
        self.had_cast=False;self.result_seen=False;self.failed_baits.clear();self.last_bait=None
        self.message='已开启 · 切回游戏后开始循环；F8 取消'

    def stop(self,message='已手动停止全自动钓鱼'):
        self.enabled=False;self.message=message
        self.inputs.release()

    def tick(self):
        if not self.enabled:return
        try:self._tick()
        except Exception:
            self.stop('全自动钓鱼发生异常，已停止输入')
            raise

    def _tick(self):
        now=self.clock();self.inputs.tick(now)
        if self.inputs.cancelled():self.stop();return
        if not self.inputs.foreground():
            self.inputs.release()
            if self.paused_at is None:self.paused_at=now
            self.message='已暂停 · 切回游戏继续；F8 取消';return
        if self.paused_at is not None:
            self.state_since+=now-self.paused_at
            self.last_action=now;self.paused_at=None
        status=self.assist.status()
        if not status or status['object']!=self.object or status['state'] in (26,27,28):
            self.stop('已离开钓鱼界面，全自动循环停止');return
        state=status['state']
        if state!=self.state:
            if state==3 and (self.had_cast or self.result_seen):
                if not self.result_seen and self.last_bait is not None:self.failed_baits.add(self.last_bait)
                self.had_cast=False;self.result_seen=False
            self.state=state;self.state_since=now;self.nav_marker=None;self.nav_attempts=0
        if 4<=state<=15:self.had_cast=True
        if status.get('no_bite'):
            if self.last_bait is not None:self.failed_baits.add(self.last_bait)
            if self.last_bait is not None and not self.result_seen:
                self.rounds+=1;self.result_seen=True
        if 16<=state<=25 and self.had_cast and not self.result_seen:
            self.rounds+=1;self.result_seen=True
            if status['result'] and self.last_bait is not None:self.failed_baits.add(self.last_bait)
        if now-self.state_since>(180 if state==6 else 90):
            self.stop('钓鱼界面长时间未推进，循环已停止，请检查游戏提示');return
        if self.inputs.held or now-self.last_action<0.6 or now-self.state_since<0.4:return
        if status['pending'] not in (-1,state):return
        key=None
        if state==3:
            menu=self.assist.bait_menu(status,self.quantity)
            if menu is None:return
            usable=[r for r in menu['rows'] if r['usable'] and r['id'] not in self.failed_baits]
            if not usable:
                if now-self.state_since<1.5:return
                suitable={r['id'] for r in menu['rows'] if r.get('suitable') and r['id'] not in self.failed_baits}
                rods=self.assist.rod_choices(status,self.quantity) if suitable else None
                target=next((r for r in rods['rods'] if r['baits']&suitable),None) if rods else None
                if not target:
                    self.stop('已跳过没有咬钩的鱼饵，没有其他适用组合，循环已停止' if self.failed_baits else '没有与已拥有鱼竿匹配的有库存鱼饵，循环已停止');return
                if target['index']==rods['selected']:return
                marker=('rod',rods['selected'],target['index'])
                if marker!=self.nav_marker:self.nav_marker=marker;self.nav_attempts=0
                self.nav_attempts+=1
                if self.nav_attempts>8:self.stop('鱼竿菜单未响应切换按键，循环已停止');return
                right=target['index']>rods['selected'];fallback=self.nav_attempts>2
                key=(0x45 if right else 0x51) if fallback else (0x27 if right else 0x25)
                self.message=f'自动切换已拥有的匹配鱼竿 · {target["id"]}'
            else:
                key=self._choose_bait(menu,usable)
        elif state in CONFIRM_STATES:
            key=self.inputs.confirm_key
            self.message='没有上钩迹象 · 自动确认，返回后换饵继续' if status.get('no_bite') else f'自动确认／下一轮 · 已完成 {self.rounds} 轮'
        else:self.message=f'自动钓鱼中 · 已完成 {self.rounds} 轮'
        if key is None:return
        # Re-read immediately before input; never confirm after a scene change.
        latest=self.assist.status()
        if not latest or (latest['object'],latest['state'],latest['pending'])!=(status['object'],state,status['pending']):return
        if self.inputs.press(key,now):self.last_action=now

    def _choose_bait(self,menu,usable):
        selected=menu['selected']
        current=next((r for r in usable if r['index']==selected),None)
        if current:
            self.last_bait=current['id'];key=self.inputs.confirm_key
            self.nav_attempts=0;self.message=f'自动选饵／抛竿 · 鱼饵 {current["id"]}，剩余 {current["quantity"]}'
        else:
            target=next((r for r in usable if r['index']>selected),usable[0])
            marker=(selected,target['index'])
            if marker!=self.nav_marker:self.nav_marker=marker;self.nav_attempts=0
            self.nav_attempts+=1
            if self.nav_attempts>8:self.stop('选饵菜单未响应方向键，循环已停止');return
            # Native menus accept arrows or W/S. Retry with W/S if needed.
            down=selected<target['index'];fallback=self.nav_attempts>2
            key=(0x53 if down else 0x57) if fallback else (0x28 if down else 0x26)
            self.message=f'自动切换有库存鱼饵 · 目标 {target["id"]}'
        return key
