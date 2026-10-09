import {useEffect, useMemo, useState} from 'react';
import {combineMessages, eventTasks, pendingConfirmations} from './native';
import type {Message, NativeEvent} from './native';
import {readHistory, streamSession} from './nativeApi';
import type {SessionRef} from './nativeApi';

export function useSessionFeed(user: string, ref: SessionRef) {
  const key = `${user}:${ref.agent_id}:${ref.id}`;
  const [state,setState] = useState<{key:string; history:Message[]; events:NativeEvent[]; error:string|null; connected:boolean; lastTaskUpdateAt:number|null}>({key,history:[],events:[],error:null,connected:false,lastTaskUpdateAt:null});
  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    let refreshRunning = false;
    const refresh = async () => {
      if (refreshRunning || signal.aborted) return;
      refreshRunning = true;
      try {
        const history = await readHistory(user,ref,signal);
        if (!signal.aborted) setState(s => s.key === key ? {...s,history} : s);
      } catch (error) {
        if (!signal.aborted) setState(s => ({...s,error:String(error)}));
      } finally {refreshRunning=false;}
    };
    setState({key,history:[],events:[],error:null,connected:false,lastTaskUpdateAt:null});
    void refresh();
    const timer = window.setInterval(() => void refresh(),2500);
    const listen = async () => {
      while (!signal.aborted) {
        try {
          await streamSession(user,ref,signal,event => {
            if (signal.aborted) return;
            const taskUpdateAt = eventTasks(event) !== undefined ? performance.now() : null;
            setState(s => s.key !== key || s.events.some(e => e.id === event.id) ? s : {...s,events:[...s.events,event],lastTaskUpdateAt:taskUpdateAt??s.lastTaskUpdateAt});
            if (event.type === 'REPLY_END' || event.type === 'REQUIRE_USER_CONFIRM') void refresh();
          }, () => {
            if (!signal.aborted) setState(s => ({...s,connected:true,error:null}));
            void refresh();
          });
          if (!signal.aborted) setState(s => ({...s,connected:false,error:'事件流已断开，正在重连…'}));
        } catch (error) {
          if (!signal.aborted) setState(s => ({...s,connected:false,error:String(error)}));
        }
        if (!signal.aborted) await new Promise<void>(resolve => {
          const done = () => {clearTimeout(wait); signal.removeEventListener('abort',done); resolve();};
          const wait = setTimeout(done,1500); signal.addEventListener('abort',done,{once:true});
        });
      }
    };
    void listen();
    return () => {controller.abort(); clearInterval(timer);};
  },[key,user,ref.id,ref.agent_id]);
  const active = state.key === key ? state : {history:[],events:[],error:null,connected:false,lastTaskUpdateAt:null};
  const messages = useMemo(() => combineMessages(active.history,active.events),[active.history,active.events]);
  const confirmations = useMemo(() => pendingConfirmations(active.history,active.events),[active.history,active.events]);
  return {...active,messages,confirmations};
}
