"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ListingPage } from "@/components/Explore";
import { DetailSkeleton } from "@/components/Skeleton";

function Inner() {
  const id = useSearchParams().get("id");
  return id ? <ListingPage id={id} /> : <div className="page"><p className="muted">No campaign selected.</p></div>;
}

export default function Page() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <Inner />
    </Suspense>
  );
}
