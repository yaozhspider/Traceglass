import {createContext,useContext} from 'react';
import {LogOut,Settings,UserRound} from 'lucide-react';
import type {AccountSession} from './accountApi';

export const AccountContext=createContext<{session:AccountSession;openModels:()=>void;logout:()=>void;signingOut:boolean}|null>(null);
export function AccountControls(){
  const value=useContext(AccountContext);if(!value)return null;
  return <div className="account-controls"><button type="button" className="secondary-button" onClick={value.openModels}><Settings size={15}/><span>模型设置</span></button>
    {value.session.user&&<><span className="account-username" title={value.session.user.username}><UserRound size={14}/>{value.session.user.username}</span>
      <button type="button" className="icon-button" aria-label="退出登录" disabled={value.signingOut} onClick={value.logout}><LogOut size={15}/></button></>}</div>;
}
