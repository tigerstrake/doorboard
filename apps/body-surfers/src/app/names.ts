// Fun random runner names for the leaderboard ("Turbo Pickle"). Short words only,
// so every combination fits the 18-character name limit.

export const NAME_ADJECTIVES = [
  'Turbo', 'Mega', 'Sneaky', 'Funky', 'Cosmic', 'Jumpy', 'Speedy', 'Wobbly', 'Spicy', 'Fluffy',
  'Zippy', 'Bouncy', 'Dizzy', 'Groovy', 'Mighty', 'Sassy', 'Crispy', 'Jazzy', 'Lucky', 'Hyper',
  'Disco', 'Atomic', 'Super', 'Wild', 'Swift', 'Sparkly', 'Grumpy', 'Brave', 'Silly', 'Sunny',
  'Fuzzy', 'Tiny', 'Giant', 'Quirky', 'Snappy', 'Nifty', 'Peppy', 'Zesty', 'Toasty', 'Frosty',
  'Rad', 'Epic', 'Neon', 'Salty', 'Sleepy', 'Chunky', 'Goofy', 'Daring', 'Shiny', 'Rowdy',
] as const;

export const NAME_NOUNS = [
  'Pickle', 'Banana', 'Noodle', 'Taco', 'Waffle', 'Muffin', 'Pancake', 'Donut', 'Nugget', 'Potato',
  'Burrito', 'Pretzel', 'Dumpling', 'Cupcake', 'Meatball', 'Avocado', 'Penguin', 'Llama', 'Panda', 'Otter',
  'Koala', 'Raccoon', 'Hamster', 'Gecko', 'Ferret', 'Badger', 'Walrus', 'Narwhal', 'Platypus', 'Squirrel',
  'Goose', 'Moose', 'Yeti', 'Wizard', 'Comet', 'Tornado', 'Sprinkle', 'Bagel', 'Biscuit', 'Turnip',
  'Gnome', 'Robot', 'Dragon', 'Unicorn', 'Toaster', 'Pigeon', 'Lobster', 'Meteor', 'Cactus', 'Mango',
] as const;

const pick = <T>(items: readonly T[], random: () => number): T =>
  items[Math.min(items.length - 1, Math.floor(random() * items.length))];

/** "Adjective Noun", avoiding names in `taken` when possible. */
export function funName(random: () => number = Math.random, taken: ReadonlySet<string> = new Set()): string {
  let name = '';
  for (let attempt = 0; attempt < 16; attempt++) {
    name = `${pick(NAME_ADJECTIVES, random)} ${pick(NAME_NOUNS, random)}`;
    if (!taken.has(name)) return name;
  }
  return name;
}
