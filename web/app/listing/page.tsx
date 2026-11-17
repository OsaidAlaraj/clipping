"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ListingPage } from "@/components/Explore";

function Inner() {
  const id = useSearchParams().get("id");
  return id ? <ListingPage id={id} /> : <div className="page"><p className="muted">No campaign selected.</p></div>;
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Inner />
    </Suspense>
  );
}
