export const advancePresentationSettled = (
  presentation: { destination: string; sourceRevision: string; target: string },
  searchIdentity: string,
  locationRevision: string,
  selectedDestination: string | undefined,
) =>
  (searchIdentity === presentation.target && presentation.destination === selectedDestination) ||
  (locationRevision !== presentation.sourceRevision && searchIdentity !== presentation.target)
