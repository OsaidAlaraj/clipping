"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import Transcript, { TranscriptSkeleton } from "@/components/Transcript";

function TranscriptPage() {
  const id = useSearchParams().get("id");
  return id ? <Transcript key={id} sourceId={id} /> : <div className="page"><p className="muted">No transcript selected.</p></div>;
}

export default function Page() {
  return (
    <Suspense fallback={<TranscriptSkeleton />}>
      <TranscriptPage />
    </Suspense>
  );
}
