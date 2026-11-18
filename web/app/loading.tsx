import { ListSkeleton } from "@/components/Skeleton";

/** Shown while a screen's code loads. It has the shape of a page, so the real screen replaces it without a jump. */
export default function Loading() {
  return <ListSkeleton title="" rows={3} />;
}
