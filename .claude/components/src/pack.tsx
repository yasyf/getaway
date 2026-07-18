import { Itinerary } from './Itinerary';
import { Flight } from './Flight';
import { Availability } from './Availability';
import { Stay } from './Stay';
import { Booking } from './Booking';

// Default export = the pack module. The host qualifies these bare names with the
// manifest's pack name (getaway.itinerary, getaway.flight, …).
export default {
  hostApi: 2,
  blocks: {
    itinerary: Itinerary,
    flight: Flight,
    availability: Availability,
    stay: Stay,
    booking: Booking,
  },
};
