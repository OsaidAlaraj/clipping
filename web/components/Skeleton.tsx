// Placeholders that keep the footprint of the screen they stand for, so nothing jumps when the data arrives.

const Sk = ({ w = "100%", h = 14, r }: { w?: string | number; h?: number; r?: number | string }) => <i className="sk" style={{ width: w, height: h, borderRadius: r }} />;

function CardSk() {
  return (
    <div className="card campaign stack" aria-hidden="true">
      <div className="row"><Sk w={42} h={42} r={21} /><div className="grow stack" style={{ gap: 6 }}><Sk w="55%" /><Sk w="35%" h={11} /></div><Sk w={64} h={22} r={11} /></div>
      <Sk w={120} h={30} />
      <div className="stack" style={{ gap: 6 }}><Sk h={6} /><Sk w="80%" h={11} /></div>
      <Sk h={14} />
    </div>
  );
}

export function HomeSkeleton() {
  return (
    <div className="page home-grid" aria-busy="true" aria-label="Loading Home">
      <div className="stack home-main" style={{ gap: 14 }}>
        <div className="stories">{[0, 1, 2, 3, 4].map((n) => <div key={n} className="story"><Sk w={64} h={64} r="50%" /><Sk w={44} h={10} /></div>)}</div>
        <div className="card tiles">{[0, 1, 2, 3].map((n) => <div key={n} className="tile"><Sk w={52} h={26} /><Sk w={70} h={11} /></div>)}</div>
        <div className="stack"><Sk w={220} h={20} />{[0, 1, 2].map((n) => <CardSk key={n} />)}</div>
      </div>
      <aside className="stack home-side"><div className="card stack"><Sk w="60%" h={18} /><Sk /><Sk /></div></aside>
    </div>
  );
}

export function ExploreSkeleton() {
  return (
    <div className="page" aria-busy="true" aria-label="Loading Explore">
      <div className="row between"><h1>Explore</h1><Sk w={96} h={40} r={20} /></div>
      <Sk h={48} r={24} />
      <div className="row between"><Sk w={220} h={48} r={24} /><Sk w={120} h={48} r={24} /></div>
      {[0, 1, 2, 3].map((n) => <CardSk key={n} />)}
    </div>
  );
}

/** Just the cards, for a screen that already shows its own header while the list loads. */
export function RowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, n) => (
        <div key={n} className="card stack" aria-hidden="true"><div className="row between"><Sk w="45%" h={18} /><Sk w={84} h={22} r={11} /></div><Sk w="70%" h={12} /><Sk w="55%" h={12} /></div>
      ))}
    </>
  );
}

export function ListSkeleton({ title, rows = 4 }: { title: string; rows?: number }) {
  return (
    <div className="page" aria-busy="true" aria-label={`Loading ${title}`}>
      <div className="page-head">{title ? <h1>{title}</h1> : <Sk w={120} h={32} />}</div>
      {Array.from({ length: rows }, (_, n) => (
        <div key={n} className="card stack" aria-hidden="true"><div className="row between"><Sk w="45%" h={18} /><Sk w={84} h={22} r={11} /></div><Sk w="70%" h={12} /><Sk w="55%" h={12} /></div>
      ))}
    </div>
  );
}

export function DetailSkeleton() {
  return (
    <div className="page" style={{ maxWidth: 860 }} aria-busy="true" aria-label="Loading">
      <div className="card stack" style={{ gap: 14 }}>
        <div className="row"><Sk w={56} h={56} r={28} /><div className="grow stack" style={{ gap: 6 }}><Sk w="60%" h={22} /><Sk w="40%" h={12} /></div></div>
        <div className="tiles">{[0, 1, 2, 3].map((n) => <div key={n}><Sk w={70} h={30} /><Sk w={90} h={11} /></div>)}</div>
        <Sk h={44} r={22} />
      </div>
      <Sk w={260} h={48} r={24} />
      {[0, 1].map((n) => <div key={n} className="card stack"><Sk w="40%" h={18} /><Sk /><Sk w="80%" /></div>)}
    </div>
  );
}

/** The review feed is full screen and dark, so its placeholder is the same black card with the same top bar and caption block. */
export function FeedSkeleton() {
  return (
    <div className="feed" aria-busy="true" aria-label="Loading the feed">
      <div className="top"><span className="name"><i className="sk dark" style={{ width: 140, height: 16 }} /></span><span className="row"><i className="sk dark" style={{ width: 64, height: 28, borderRadius: 14 }} /></span></div>
      <div className="feed-card">
        <div className="bottom"><i className="sk dark" style={{ width: "60%", height: 14 }} /><i className="sk dark" style={{ width: "90%", height: 14 }} /><i className="sk dark" style={{ width: "40%", height: 12 }} /></div>
      </div>
    </div>
  );
}
