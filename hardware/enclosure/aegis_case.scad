// AEGIS responder device enclosure (SIMULATION ONLY demo hardware)
//
// A two-part case for the Arduino UNO R4 WiFi that straps onto the lower half
// of the power bank (Yoobao PDL20, about 143 x 73 x 29 mm), leaving the power
// bank's % display visible.
//
//   shield = true  : Grove Base Shield stacked on the Arduino. The shield covers
//                    the LED matrix, so the lid has two pockets instead of a
//                    window: the button, and a Grove LED (or buzzer) for alerts.
//   shield = false : bare Arduino with a jumper cable; lid has a matrix window.
//
//   body : floor + walls + strap wings. Prints as modelled, no supports.
//   lid  : window over the LED matrix, pocket for the Grove button, AEGIS
//          branding. Prints upside down (top face on the bed), no supports.
//
// Export:  openscad -D "part=\"body\"" -o aegis_body.stl aegis_case.scad
//          openscad -D "part=\"lid\""  -o aegis_lid.stl  aegis_case.scad

part = "assembly";   // "assembly" | "body" | "lid" | "plate" (both, print layout)
                     // version 2 (cradle): "assembly2" | "body2" | "lid2" | "plate2"
$fn = 48;

// ---- things to check with a ruler before printing ---------------------------
bank       = [143, 73, 29];       // power bank: length, width, thickness
board      = [68.6, 53.4, 1.6];   // Arduino UNO R4 WiFi PCB
shield     = false;               // Grove Base Shield fitted? (true = taller case)
headroom   = shield ? 34 : 24;    // space above the Arduino PCB for shield + plugs
// LED matrix window, in board coordinates (x from the USB end, y from the
// power/analog header edge). One window covers both boards, so either board
// can go in either case:
//   UNO R4 WiFi matrix: about x 34-64, y 14-40 (centre right)
//   UNO Q matrix:       about x 32-62, y 4.5-21 (lower right, from the top-view photo)
window     = [[31, 3.5], [66, 42]];
// Grove button module (20 x 20 mm PCB) + its plug, in board coordinates.
button_pcb = 20;                  // square part of the module
button_lugs = 5;                  // extra width for the mounting ears (24 mm across) + slack
pocket_y   = [9, 46];             // module sits at the low-y end
slot_y     = [34, 45];            // plug and cable drop into the case here
// x start of each pocket: button always; alert LED only with the shield
pockets_x  = shield ? [4, 37] : [4];
strap_w    = 20;                  // velcro strap width

// ---- print tuning -----------------------------------------------------------
clr        = 0.6;    // gap around the PCB
wall       = 1.6;    // 4 lines with a 0.4 mm nozzle
floor_t    = 1.6;
lid_t      = 2.0;
lip_t      = 1.4;    // lid lip that pushes into the body
lip_h      = 5;
lip_gap    = 0.2;    // raise to 0.3 if the lid is too tight
standoff_h = 4;
pin_d      = 2.8;    // locating pins for the 3.2 mm board holes
emboss     = 0.8;
brand      = "AEGIS";
font       = "Liberation Sans:style=Bold";

// UNO mounting holes (board coordinates). Pins on two, flat posts on the rest.
holes     = [[13.97, 2.54], [15.24, 50.8], [66.04, 7.62], [66.04, 35.56]];
pin_holes = [0, 3];

// ---- derived ----------------------------------------------------------------
cav    = [board.x + 2 * clr, board.y + 2 * clr, standoff_h + board.z + headroom];
out    = [cav.x + 2 * wall, cav.y + 2 * wall, floor_t + cav.z];
b0     = [wall + clr, wall + clr, floor_t + standoff_h];   // board origin
pcb_top = b0.z + board.z;
plate_w = bank.y + 2 + 4 + 2 * 3;      // strap slots sit just outside the bank
wing    = (plate_w - out.y) / 2;
notch_y = [b0.y + 1, b0.y + board.y - 4];   // open end: USB-C and the barrel jack overhang the PCB
door_z  = pcb_top + 13;                     // lid tab closes the notch above this

// ---- version 2: a cradle that holds the power bank between side walls ---------
// Rivacase VA2612 layout: display and ports at the top, power button on one side
// near the top, two built-in cables stowed in grooves down the middle of the back.
// So: the Arduino sits on the FRONT of the bank, over its lower part; the lips
// hold only the outer edges of the back, clear of the cables; the bank's bottom
// end rests against two corner stops at the Arduino's USB end, where the built-in
// USB-C cable wraps round to the Arduino; the top 45 mm (display, ports, button)
// sticks out of the far end. No straps. The body prints standing on its USB end.
bank2  = [118, 70, 36];            // Rivacase VA2612: length, width, thickness
bclr   = 0.6;                      // gap each side of the power bank (loose on purpose)
cwall  = 2.0;                      // cradle side walls
flange = [5, 2];                   // lips under the power bank: width, thickness
ch_in  = [bank2.y + 2 * bclr, bank2.z + 1.0];
ch_w   = ch_in[0] + 2 * cwall;
dz2    = flange[1] + ch_in[1];     // the Arduino shell's floor is the shelf above the bank
yo2    = (ch_w - out.y) / 2;

module rr(size, r = 3) {   // rounded rectangle prism
    hull() for (x = [r, size.x - r], y = [r, size.y - r])
        translate([x, y, 0]) cylinder(r = r, h = size.z);
}

module side_brand() {
    translate([out.x / 2, 0, floor_t + (out.z - floor_t) / 2])
        rotate([90, 0, 0]) linear_extrude(emboss)
            text(brand, size = 13, font = font, halign = "center", valign = "center", spacing = 1.1);
}

module body() {
    difference() {
        union() {
            rr(out);
            translate([0, -wing, 0]) rr([out.x, plate_w, floor_t]);   // strap wings
            side_brand();
            translate([out.x, out.y, 0]) rotate([0, 0, 180]) side_brand();
        }
        translate([wall, wall, floor_t]) cube([cav.x, cav.y, cav.z + 1]);
        // USB-C / power end: open notch, closed at the top by the lid tab
        translate([-1, notch_y[0], b0.z]) cube([wall + 2, notch_y[1] - notch_y[0], out.z]);
        // strap slots, two per wing
        for (x = [out.x * 0.22, out.x * 0.78], y = [-wing + 3, out.y + wing - 3 - 4])
            translate([x - (strap_w + 2) / 2, y, -1]) cube([strap_w + 2, 4, floor_t + 2]);
    }
    for (i = [0 : len(holes) - 1]) translate([b0.x + holes[i].x, b0.y + holes[i].y, floor_t]) {
        cylinder(d = 5, h = standoff_h);
        if (len(search(i, pin_holes)) > 0) cylinder(d = pin_d, h = standoff_h + board.z + 1);
    }
}

module body2() {
    translate([0, yo2, dz2]) {
        difference() {                       // Arduino shell, open at both ends
            cube(out);
            translate([-1, wall, floor_t]) cube([out.x + 2, cav.y, cav.z + 1]);
        }
        for (y = [wall, wall + cav.y - (clr + 1.2)])   // rails under the long PCB edges
            translate([0, y, floor_t]) cube([out.x, clr + 1.2, standoff_h]);
    }
    translate([0, 0, dz2]) cube([out.x, ch_w, floor_t]);                       // shelf
    for (y = [0, ch_w - cwall]) translate([0, y, 0]) cube([out.x, cwall, dz2 + floor_t]);
    for (y = [0, ch_w - cwall - flange[0]]) translate([0, y, 0]) cube([out.x, cwall + flange[0], flange[1]]);
    for (y = [0, ch_w - 14]) translate([0, y, 0]) cube([2, 14, flange[1] + 7]);  // corner stops
    for (s = [0, 1]) translate([s * out.x, s * ch_w, 0]) rotate([0, 0, 180 * s])
        translate([out.x / 2, 0, (dz2 + floor_t) / 2]) rotate([90, 0, 0]) linear_extrude(emboss)
            text(brand, size = 15, font = font, halign = "center", valign = "center", spacing = 1.1);
}

module lid(legs = false) {
    pw = button_pcb + button_lugs;
    pocket_depth = 3.0;
    top = out.z + lid_t;
    difference() {
        union() {
            translate([0, 0, out.z]) rr([out.x, out.y, lid_t]);
            // lip
            translate([wall + lip_gap, wall + lip_gap, out.z - lip_h]) difference() {
                cube([cav.x - 2 * lip_gap, cav.y - 2 * lip_gap, lip_h]);
                translate([lip_t, lip_t, -1])
                    cube([cav.x - 2 * lip_gap - 2 * lip_t, cav.y - 2 * lip_gap - 2 * lip_t, lip_h + 2]);
            }
            // tab that closes the top of the USB notch
            if (!legs) translate([0, notch_y[0] + 0.3, door_z])
                cube([wall, notch_y[1] - notch_y[0] - 0.6, out.z - door_z]);
            // version 2: the end is open, so the door is full width and two legs
            // reach down beside the connectors to stop the board sliding out
            if (legs) {
                translate([0, wall + 0.3, door_z]) cube([wall, cav.y - 0.6, out.z - door_z]);
                translate([0, wall + 0.3, b0.z]) cube([wall, clr + 2.8 - 0.3, door_z - b0.z + 1]);
                translate([0, b0.y + board.y - 4, b0.z]) cube([wall, 4 + clr - 0.3, door_z - b0.z + 1]);
                // far end: a full door down to just above the PCB
                translate([out.x - wall, wall + 0.3, pcb_top + 0.5])
                    cube([wall, cav.y - 0.6, out.z - pcb_top - 0.5]);
            }
            // thicker floor under each pocket
            for (px = pockets_x)
                translate([b0.x + px - 1.2, b0.y + pocket_y[0] - 1.2, top - pocket_depth - 1.2])
                    cube([pw + 2.4, pocket_y[1] - pocket_y[0] + 2.4, pocket_depth + 1.2 - lid_t + 0.01]);
        }
        // LED matrix window (only useful without the shield)
        if (!shield)
            translate([b0.x + window[0].x, b0.y + window[0].y, out.z - 1])
                rr([window[1].x - window[0].x, window[1].y - window[0].y, lid_t + 2], 2.5);
        // module pockets (open at the top) and cable slots (through)
        for (px = pockets_x) {
            translate([b0.x + px, b0.y + pocket_y[0], top - pocket_depth])
                cube([pw, pocket_y[1] - pocket_y[0], pocket_depth + 1]);
            translate([b0.x + px + (pw - 14) / 2, b0.y + slot_y[0], out.z - lip_h - 1])
                cube([14, slot_y[1] - slot_y[0], lip_h + lid_t + 2]);
        }
        // branding, engraved
        translate([out.x / 2, b0.y + (shield ? 49.6 : 50.2), top - emboss])
            linear_extrude(emboss + 1)
                text(shield ? brand : str(brand, " RESPONDER"), size = shield ? 5.6 : 4.8, font = font,
                     halign = "center", valign = "center", spacing = 1.15);
        if (shield) translate([out.x / 2, b0.y + 3.6, top - emboss])
            linear_extrude(emboss + 1)
                text("RESPONDER", size = 4.6, font = font, halign = "center", valign = "center", spacing = 1.15);
    }
}

if (part == "body") body();
else if (part == "lid") translate([0, out.y, out.z + lid_t]) rotate([180, 0, 0]) lid();
else if (part == "plate") {
    body();
    translate([0, -wing - 20, out.z + lid_t]) rotate([180, 0, 0]) lid();
} else if (part == "body2") translate([dz2 + out.z, 0, 0]) rotate([0, -90, 0]) body2();
else if (part == "lid2") translate([0, out.y, out.z + lid_t]) rotate([180, 0, 0]) lid(legs = true);
else if (part == "plate2") {
    translate([dz2 + out.z, 0, 0]) rotate([0, -90, 0]) body2();
    translate([0, -20, out.z + lid_t]) rotate([180, 0, 0]) lid(legs = true);
} else if (part == "assembly2") {
    color("#20262e") body2();
    color("#d8dde3") translate([0, yo2, dz2]) lid(legs = true);
    %translate([2, cwall + bclr, flange[1]]) rr(bank2, 8);
    %translate([b0.x, yo2 + b0.y, dz2 + b0.z]) cube(board);
} else {
    color("#20262e") body();
    color("#d8dde3") lid();
    // ghosts: power bank, PCB, button module
    %translate([out.x + 2 - bank.x, (out.y - bank.y) / 2, -bank.z]) rr(bank, 8);
    %translate(b0) cube(board);
    for (px = pockets_x) %translate([b0.x + px + button_lugs / 2, b0.y + pocket_y[0] + 0.3, out.z + lid_t - 3])
        cube([button_pcb, button_pcb, 1.6]);
}
