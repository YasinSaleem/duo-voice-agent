import { supabaseAdmin } from './db/supabase';

async function check() {
  console.log("Fetching a single row from scenarios...");
  const { data, error } = await supabaseAdmin.from('scenarios').select('*').order('title', { ascending: true }).limit(1);
  if (error) {
    console.error("Error fetching scenarios:", error);
  } else {
    console.log("Success! Row data:", data);
  }
}

check();
