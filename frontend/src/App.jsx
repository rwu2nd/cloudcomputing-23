import AdminPanel from './AdminPanel.jsx';
import { useCallback, useEffect, useState } from 'react';
import './index.css';

// Base URL for API calls. In Docker/Kubernetes this is "/api" and nginx forwards
// it to the backend. Override at build time with VITE_API_BASE_URL if needed.
const API = import.meta.env.VITE_API_BASE_URL ?? '/api';
const sizes = {S:'Small',M:'Medium',L:'Large',XL:'Extra large'};
// Temporary luggage-only demo mode until the shared service + DB support luggage bookings.
// Change VITE_LUGGAGE_DEMO=false after the actual luggage endpoints are integrated.
const LUGGAGE_DEMO = import.meta.env.VITE_LUGGAGE_DEMO !== 'false';
const DEMO_LOCATIONS = ['A','B','C'];
const DEMO_CAPACITY = 2; // Sample capacity per size at each location; NOT real availability.
const STORAGE_KEY = 'parcelpoint-luggage-demo-v1';
function getDemoBookings() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(stored) ? stored.filter(x => x && x.id && DEMO_LOCATIONS.includes(x.location)) : [];
  } catch { return []; }
}
function demoAvailability(bookings, location, size) {
  return Math.max(0, DEMO_CAPACITY - bookings.filter(b => b.location === location && b.size === size && b.status === 'RESERVED').length);
}
async function request(path, opts={}) {
  let response;
  try { response=await fetch(`${API}${path}`,opts); } catch { throw Error('Unable to connect to the server.'); }
  const data=await response.json().catch(()=>null);
  if(!response.ok) throw Error(typeof data?.detail==='string'?data.detail:'Request failed. Please try again.');
  return data;
}
const isPending=s=>!['CANCELLED','FULFILLED','UNALLOCATED'].includes(s);
function Pill({children,tone='neutral'}){return <span className={`pill ${tone}`}>{children}</span>}
function Arrow(){return <span aria-hidden="true">↗</span>}
function Notice({children}){return <div className="notice" role="status">{children}</div>}
function SectionChoice({icon,title,sub,onClick}){return <button className="choice" onClick={onClick}><span className="choice-icon">{icon}</span><strong>{title}</strong><small>{sub}</small><span className="choice-arrow"><Arrow/></span></button>}

export default function App(){
  const [tab,setTab]=useState(location.pathname.startsWith('/admin')?'testing':'user');
  const [screen,setScreen]=useState('choose');
  const [kind,setKind]=useState('collect');
  const [customer,setCustomer]=useState('');
  const [lookup,setLookup]=useState('');
  const [records,setRecords]=useState([]);
  const [recordsError,setRecordsError]=useState('');
  const [stations,setStations]=useState([]);
  const [stationError,setStationError]=useState('');
  const [size,setSize]=useState('M');
  const [station,setStation]=useState('');
  const [count,setCount]=useState(null);
  const [checking,setChecking]=useState(false);
  const [error,setError]=useState('');
  const [saving,setSaving]=useState(false);
  const [receipt,setReceipt]=useState(null);
  const [testMode,setTestMode]=useState('load');
  const [luggageBookings,setLuggageBookings]=useState(getDemoBookings);
  // Parcel collection (receive parcel / unlock locker) state
  const [parcels,setParcels]=useState([]);
  const [selectedParcel,setSelectedParcel]=useState(null);
  const [password,setPassword]=useState('');
  const [collectionMessage,setCollectionMessage]=useState('');
  const [collectionOk,setCollectionOk]=useState(false);
  const [loadingParcels,setLoadingParcels]=useState(false);
  const [unlocking,setUnlocking]=useState(false);
  useEffect(()=>{if(LUGGAGE_DEMO){try{localStorage.setItem(STORAGE_KEY,JSON.stringify(luggageBookings))}catch{ /* storage may be unavailable */ }}},[luggageBookings]);

  const loadStations=useCallback(async()=>{
    try{setStationError(''); const s=await request('/stations');setStations((Array.isArray(s)?s:[]).filter(x=>x.active!==false).slice(0,3).map((x,i)=>({...x,name:`Location ${'ABC'[i]}`})));}
    catch(e){setStationError(e.message)}
  },[]);
  useEffect(()=>{loadStations()},[loadStations]);
  useEffect(()=>{if(kind==='luggage'&&LUGGAGE_DEMO){setCount(null);return;}if(!station||!size){setCount(null);return;}
    let active=true;setChecking(true);setCount(null);
    const refresh=async()=>{try{const result=await request(`/stations/${encodeURIComponent(station)}/compartments`);if(active){setCount(result.filter(x=>x.active&&!x.reserved&&x.size===size).length);setStationError('')}}catch(e){if(active){setCount(null);setStationError(e.message)}}finally{if(active)setChecking(false)}};
    refresh();const interval=setInterval(refresh,10000);return()=>{active=false;clearInterval(interval)};
  },[station,size]);
  async function loadRecords(id=lookup){
    if(!id.trim()){setRecords([]);return}
    setRecordsError('');
    try{const all=await request('/reservations');setRecords((Array.isArray(all)?all:[]).filter(r=>r.customer_id===id.trim()));}
    catch(e){setRecordsError(e.message)}
  }
  // Retrieve parcels that are ready for collection
  async function loadParcels(){
    setLoadingParcels(true);setCollectionMessage('');setCollectionOk(false);
    try{
      const data=await request('/parcels/collection');
      setParcels(Array.isArray(data)?data:[]);
      setSelectedParcel(null);setPassword('');
    }catch(e){setCollectionMessage(e.message)}
    finally{setLoadingParcels(false)}
  }
  // Verify the collection password and unlock the locker
  async function unlockLocker(){
    if(!selectedParcel||!password.trim()){setCollectionOk(false);setCollectionMessage('Please enter your collection password.');return}
    setUnlocking(true);
    try{
      await request(`/reservations/${selectedParcel.reservation_id}/unlock`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});
      setParcels(prev=>prev.filter(p=>p.reservation_id!==selectedParcel.reservation_id));
      setSelectedParcel(null);setPassword('');
      setCollectionOk(true);setCollectionMessage('Locker unlocked successfully!');
    }catch(e){setCollectionOk(false);setCollectionMessage(e.message)}
    finally{setUnlocking(false)}
  }
  function move(next){setError('');setScreen(next);if(next==='collect'||next==='return'){setKind(next);loadRecords();}if(next==='new'){setCount(null);setSize('M');setStation('');}if(next==='receive'){setSelectedParcel(null);setPassword('');setCollectionMessage('');setCollectionOk(false);loadParcels();}}
  function changeTab(next){setTab(next);window.history.pushState({},'',next==='testing'?'/admin':'/');}
  useEffect(()=>{const listener=()=>setTab(location.pathname.startsWith('/admin')?'testing':'user');addEventListener('popstate',listener);return()=>removeEventListener('popstate',listener)},[]);
  async function book(e){e.preventDefault();setError('');if(!customer.trim()||!station)return setError('Enter your customer ID and select a location.');
    if(kind==='luggage'&&LUGGAGE_DEMO){
      if(!DEMO_LOCATIONS.includes(station))return setError('Choose Location A, B or C.');
      if(demoAvailability(luggageBookings,station,size)<1)return setError('No demo lockers of this size are available here.');
      const record={id:`LG-DEMO-${Date.now().toString(36).toUpperCase()}-${Math.random().toString(36).slice(2,6).toUpperCase()}`,customer_id:customer.trim(),size,location:station,status:'RESERVED',created_at:new Date().toISOString()};
      setLuggageBookings(old=>[record,...old]);setLookup(customer.trim());setReceipt(record);move('luggage-confirmed');return;
    }
    if(kind!=='collect')return;
    if(!count)return setError('No suitable locker is currently available. Refresh or choose another location.');
    setSaving(true);try{const data=await request('/reservations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({customer_id:customer.trim(),preferred_station_id:Number(station),backup_station_ids:[],required_size:size})});setReceipt(data);move('confirmed');}
    catch(e){setError(e.message)}finally{setSaving(false)}
  }
  const stationLabel=id=>stations.find(s=>String(s.id)===String(id))?.name||`Location ${id}`;
  const pendingRecords=records.filter(r=>isPending(r.status));
  const myLuggage = luggageBookings.filter(b=>lookup.trim()&&b.customer_id===lookup.trim());
  const luggageFree = kind==='luggage'&&LUGGAGE_DEMO&&station ? demoAvailability(luggageBookings,station,size) : null;
  return <div className="app">
    <header className="top"><div className="top-inner"><div className="brand"><span className="brand-symbol">▦</span><span>parcel<span className="brand-blue">point</span><small>SMART LOCKER SYSTEM</small></span></div><nav aria-label="Main tabs"><button className={tab==='user'?'active':''} onClick={()=>changeTab('user')}>User</button><button className={tab==='testing'?'active':''} onClick={()=>changeTab('testing')}>Admin</button></nav></div></header>
    <div style={{display:tab==='user'?'block':'none'}}><main className="main">
      {screen==='choose'&&<><div className="overline">BOOK A LOCKER</div><h1>What would you like to store?</h1><p className="subtitle">Choose a service to get started.</p><div className="choice-grid"><SectionChoice icon="▣" title="Parcel" sub="Collect deliveries or return items" onClick={()=>move('parcel')}/><SectionChoice icon="▤" title="Luggage" sub="Store your bags in a locker" onClick={()=>{setKind('luggage');move('luggage')}}/></div></>}
      {screen==='parcel'&&<><button className="back" onClick={()=>move('choose')}>← Back</button><div className="overline">PARCEL SERVICES</div><h1>What would you like to do?</h1><p className="subtitle">Manage your delivery or return.</p><div className="choice-grid"><SectionChoice icon="↓" title="Collect parcel" sub="View pending collections or book a new delivery" onClick={()=>move('collect')}/><SectionChoice icon="↩" title="Return parcel" sub="View pending returns or request a new return" onClick={()=>move('return')}/></div></>}
      {screen==='luggage'&&<><button className="back" onClick={()=>move('choose')}>← Back</button><div className="overline">LUGGAGE STORAGE</div><h1>My Luggage Bookings</h1><p className="subtitle">Check your luggage reservations or make a new booking.</p><div className="panel"><div className="lookup"><label>Customer ID<input value={lookup} onChange={e=>setLookup(e.target.value)} placeholder="Enter customer ID" maxLength={120}/></label></div>
        {LUGGAGE_DEMO&&<Notice><strong>Demo mode:</strong> Luggage bookings below are stored in this browser only. They are not shared with other computers or the database.</Notice>}
        {!lookup.trim()?<div className="empty">Enter your customer ID to view luggage bookings.</div>:!LUGGAGE_DEMO?<div className="empty">Luggage booking history will appear here once the luggage API is connected.</div>:myLuggage.length===0?<div className="empty">No luggage bookings found for this customer.</div>:<div className="booking-list">{myLuggage.map(b=><article className="booking-card" key={b.id}><div className="booking-head"><strong>{b.id}</strong><Pill tone={b.status==='RESERVED'?'good':'neutral'}>{b.status==='RESERVED'?'Reserved (demo)':'Released (demo)'}</Pill></div><p>Location {b.location} · {sizes[b.size]} ({b.size})</p><small>Created {new Date(b.created_at).toLocaleString()}</small>{b.status==='RESERVED'&&<button className="secondary-action" onClick={()=>{if(window.confirm('Release this demo luggage reservation?'))setLuggageBookings(old=>old.map(item=>item.id===b.id?{...item,status:'RELEASED'}:item))}}>Release demo booking</button>}</article>)}</div>}
      </div><button className="primary wide-action" onClick={()=>{setKind('luggage');setCustomer(lookup);move('new')}}>＋ New Luggage Booking</button></>}
      {(screen==='collect'||screen==='return')&&<><button className="back" onClick={()=>move('parcel')}>← Parcel services</button><div className="overline">{screen==='collect'?'COLLECT PARCEL':'RETURN PARCEL'}</div><h1>{screen==='collect'?'Pending collections':'Pending returns'}</h1><p className="subtitle">Enter your customer ID to check your requests.</p><div className="panel"><div className="lookup"><label>Customer ID<input value={lookup} onChange={e=>setLookup(e.target.value)} placeholder="Customer ID"/></label><button className="primary" onClick={()=>loadRecords()}>Find items</button></div>{recordsError&&<div className="error">{recordsError}</div>}
        <Notice>The current backend has reservations, but does not identify whether each reservation is a collection or return. Items are not shown as pending collections/returns until your team adds a service type and return due dates.</Notice>
        {pendingRecords.length>0&&<p className="muted">{pendingRecords.length} existing reservation(s) found, but they cannot yet be classified by service.</p>}
        <div className="empty">No verified pending {screen==='collect'?'collections':'returns'} to display.</div>
        </div>
        {screen==='collect'&&<button className="secondary-action wide-action" onClick={()=>move('receive')}>Receive a parcel (unlock locker)</button>}
        <button className="primary wide-action" onClick={()=>{setKind(screen==='collect'?'collect':'return');setCustomer(lookup);move('new')}}>{screen==='collect'?'＋ Book a new parcel delivery':'＋ New parcel return request'}</button></>}
      {screen==='receive'&&<><button className="back" onClick={()=>move('collect')}>← Collect parcel</button><div className="overline">RECEIVE PARCEL</div><h1>Receive parcel</h1><p className="subtitle">Select a parcel that is ready for collection, then enter your collection password to unlock its locker.</p><div className="panel">
        {collectionMessage&&<div className={collectionOk?'ok':'error'} role={collectionOk?'status':'alert'}>{collectionMessage}</div>}
        {selectedParcel?<>
          <h2>Enter collection password</h2>
          <div className="fact"><span>Station</span><strong>{selectedParcel.station_name}</strong></div>
          <div className="fact"><span>Compartment</span><strong>{selectedParcel.compartment_label}</strong></div>
          <label>Collection password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="Enter your password" autoComplete="off"/></label>
          <button type="button" className="primary submit" onClick={unlockLocker} disabled={unlocking}>{unlocking?'Unlocking…':'Unlock locker'}</button>
          <button type="button" className="secondary-action" onClick={()=>{setSelectedParcel(null);setPassword('');setCollectionMessage('');setCollectionOk(false)}}>Back to parcel list</button>
        </>:<>
          <h2>Available parcels</h2>
          {loadingParcels?<div className="empty">Loading parcels…</div>:parcels.length===0?<div className="empty">There are currently no parcels ready for collection.</div>:<div className="booking-list">{parcels.map(parcel=><article className="booking-card" key={parcel.reservation_id}><div className="booking-head"><strong>{parcel.station_name}</strong><Pill tone="good">Ready</Pill></div><p>Compartment {parcel.compartment_label} · {sizes[parcel.required_size]||parcel.required_size} ({parcel.required_size})</p><button type="button" className="primary" onClick={()=>{setSelectedParcel(parcel);setCollectionMessage('');setCollectionOk(false)}}>Select parcel</button></article>)}</div>}
          <button type="button" className="secondary-action" onClick={loadParcels} disabled={loadingParcels}>Refresh list</button>
        </>}
      </div></>}
      {screen==='new'&&<><button className="back" onClick={()=>move(kind==='luggage'?'luggage':kind)}>← Back</button><div className="overline">{kind==='luggage'?'LUGGAGE STORAGE':kind==='return'?'NEW RETURN REQUEST':'NEW PARCEL DELIVERY'}</div><h1>Choose your locker</h1><p className="subtitle">Select a size and location to see live locker availability. No backup locations.</p><form className="panel" onSubmit={book}><label>Customer ID<input required value={customer} onChange={e=>setCustomer(e.target.value)} placeholder="Enter customer ID" maxLength={120}/></label><div className="form-grid"><label>Locker size<select value={size} onChange={e=>setSize(e.target.value)}>{Object.entries(sizes).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>Location<select value={station} onChange={e=>setStation(e.target.value)} required><option value="">Select a location</option>{kind==='luggage'&&LUGGAGE_DEMO?DEMO_LOCATIONS.map(loc=><option key={loc} value={loc}>Location {loc}</option>):stations.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label></div>
        {!(kind==='luggage'&&LUGGAGE_DEMO)&&stationError&&<div className="error">Could not check availability: {stationError}</div>}
        {station&&(kind==='luggage'&&LUGGAGE_DEMO?<div className="availability"><Pill tone={luggageFree>0?'good':'bad'}>{luggageFree>0?'Available':'Unavailable'}</Pill><strong>{luggageFree} demo {size} locker{luggageFree===1?'':'s'} at Location {station}</strong></div>:<div className="availability">{checking?'Checking available lockers…':count===null?'Availability unavailable':count>0?<><Pill tone="good">Available</Pill><strong>{count} {size} locker{count===1?'':'s'} at {stationLabel(station)}</strong></>:<><Pill tone="bad">Unavailable</Pill><strong>No {size} lockers at {stationLabel(station)}</strong></>}</div>)}
        {kind==='luggage'&&LUGGAGE_DEMO?<Notice><strong>Demo mode:</strong> Sample availability (2 compartments per location and size). Confirming creates a browser-only booking, not a real reservation. No database requests are sent for luggage.</Notice>:<Notice>Availability is a snapshot, refreshed every 10 seconds. The server must recheck availability during booking to prevent double bookings.</Notice>}
        {kind!=='collect'&&!(kind==='luggage'&&LUGGAGE_DEMO)&&<Notice>{kind==='luggage'?'Luggage':'Parcel return'} reservations need a service-specific backend endpoint or booking type. Submission remains disabled until integration.</Notice>}
        {error&&<div className="error" role="alert">{error}</div>}
        <button className="primary submit" disabled={(kind!=='collect'&&!(kind==='luggage'&&LUGGAGE_DEMO))||!station||(kind==='luggage'&&LUGGAGE_DEMO?luggageFree===0:count===null||count===0)||saving}>{saving?'Submitting…':kind==='collect'?'Confirm parcel reservation':kind==='luggage'&&LUGGAGE_DEMO?'Confirm demo luggage booking':kind==='luggage'?'Luggage booking — awaiting API':'Return booking — awaiting API'}</button>
        </form></>}
      {screen==='luggage-confirmed'&&receipt&&<><button className="back" onClick={()=>move('luggage')}>← My Luggage Bookings</button><div className="panel confirmed"><div className="confirm-check">✓</div><div className="overline">DEMO RESERVATION</div><h1>Luggage booking created</h1><p className="subtitle">This is a browser-only demonstration, not a real locker reservation.</p><div className="fact"><span>Booking ID</span><strong>{receipt.id}</strong></div><div className="fact"><span>Location</span><strong>Location {receipt.location}</strong></div><div className="fact"><span>Size</span><strong>{sizes[receipt.size]}</strong></div><button className="primary wide-action" onClick={()=>move('luggage')}>View My Luggage Bookings</button></div></>}
      {screen==='confirmed'&&receipt&&<><button className="back" onClick={()=>move('choose')}>← Start again</button><div className="panel confirmed"><div className="confirm-check">✓</div><div className="overline">BOOKING RESPONSE</div><h1>{receipt.status==='ALLOCATED'?'Locker reserved':'Reservation submitted'}</h1><p className="subtitle">The status below comes from the backend.</p><div className="fact"><span>Reservation</span><strong>{receipt.id}</strong></div><div className="fact"><span>Status</span><Pill tone={receipt.status==='ALLOCATED'?'good':'neutral'}>{receipt.status}</Pill></div><div className="fact"><span>Size</span><strong>{sizes[receipt.required_size]}</strong></div><div className="fact"><span>Location</span><strong>{stationLabel(receipt.preferred_station_id)}</strong></div>{receipt.status!=='ALLOCATED'&&<Notice>A successful reservation request does not guarantee a locker has been allocated. Your backend reported {receipt.status}.</Notice>}</div></>}
    </main></div><div style={{display:tab==='testing'?'block':'none'}}><AdminPanel /></div>
    <footer>ParcelPoint · Distributed systems coursework prototype</footer>
  </div>;
}
