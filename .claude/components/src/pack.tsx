import { Itinerary } from './Itinerary';
import { Flight } from './Flight';
import { Availability } from './Availability';
import { OptionPicker } from './OptionPicker';
import { Stay } from './Stay';

// Default export = the pack module. The host qualifies these bare names with the
// manifest's pack name (getaway.itinerary, getaway.flight, …). The hyphenated
// key must be quoted.
export default {
  hostApi: 1,
  blocks: {
    itinerary: Itinerary,
    flight: Flight,
    availability: Availability,
    'option-picker': OptionPicker,
    stay: Stay,
  },
};
