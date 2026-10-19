import {authFetch,setCsrf,notifyAccountChange} from './transport';
const base=import.meta.env.VITE_TRACEGLASS_API ?? '/api';
export type AccountSession={auth_enabled:boolean;user:{id:string;username:string}|null;csrf_token:string|null};
export type ModelInput={base_url:string;model:string;api_key:string};
export type ModelProfile={base_url:string;model:string;has_key:boolean;
  presets:Array<{id:string;name:string;base_url:string;model:string}>;allowed_hosts:string[]};
async function api<T>(path:string,method='GET',body?:unknown):Promise<T> {
  const response=await authFetch(base+path,{method,...(body ? {body:JSON.stringify(body),headers:{'Content-Type':'application/json'}} : {})});
  if (!response.ok) {
    let message=`请求失败（${response.status}）`;
    try {const {detail}=await response.json();
      if (typeof detail==='string') message=detail;
      else if (Array.isArray(detail)) message=detail.map(item=>`${item.loc?.slice(1).join('.')}: ${item.msg}`).join('；');
    } catch { /* Do not echo proxy or provider bodies. */ }
    throw new Error(message);
  }
  return response.status===204 ? undefined as T : response.json();
}
export const accountApi={
  session:async():Promise<AccountSession>=>{
    const response=await authFetch(base+'/auth/session');
    // Compatibility with existing local preview processes; outages remain errors.
    if (response.status===404) return {auth_enabled:false,user:null,csrf_token:null};
    if (!response.ok) throw new Error('无法连接账号服务，请重试');
    const session=await response.json();setCsrf(session.csrf_token);return session;
  },
  signIn:async(register:boolean,username:string,password:string)=>{
    const session=await api<AccountSession>(register?'/auth/register':'/auth/login','POST',{username,password});
    setCsrf(session.csrf_token);notifyAccountChange();return session;
  },
  logout:async()=>{await api('/auth/logout','POST');setCsrf(null);notifyAccountChange();},
  model:()=>api<ModelProfile>('/product/model'),
  saveModel:(body:ModelInput)=>api('/product/model','PUT',body),
  testModel:(body:ModelInput)=>api<{streaming:boolean;tool_calling:boolean;message:string}>('/product/model/test','POST',body),
  discoverModels:(body:ModelInput)=>api<{models:string[]}>('/product/model/discover','POST',body),
};
